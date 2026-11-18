"""Retention / purge job (Phase D): eligibility, idempotency, failures, dry-run, batching, no audit, GETs after."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from conftest import FakeStorage, TransactionalDatabase
from evidence_fixtures import add_asset, add_session, edata  # noqa: F401  (fixture)
from jobs import purge
from jobs.common import EXIT_DEPENDENCY, EXIT_FAILURES, EXIT_OK
from repositories import maintenance_repository

PAST = datetime.now(timezone.utc) - timedelta(days=1)
FUTURE = datetime.now(timezone.utc) + timedelta(days=30)


def _set(db_conn, asset_id, **columns):
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    db_conn.execute(text(f"UPDATE media_asset SET {assignments} WHERE id = :id"), {"id": asset_id, **columns})


def _row(db_conn, asset_id):
    return dict(db_conn.execute(text("SELECT * FROM media_asset WHERE id = :i"), {"i": asset_id}).mappings().one())


def _key(db_conn, asset_id):
    return _row(db_conn, asset_id)["object_key"]


def _setup(db_conn, edata, storage, asset_key="a1_jpeg", **columns):
    asset_id = edata.assets[asset_key]
    if columns:
        _set(db_conn, asset_id, **columns)
    storage.add_object(_key(db_conn, asset_id))
    return asset_id


def _run(db_conn, storage, **kwargs):
    kwargs.setdefault("batch_size", 100)
    return purge.run_purge(TransactionalDatabase(db_conn), storage, **kwargs)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def job_log():
    """Captures evidence.jobs.purge directly (the service logger tree does not propagate to the root)."""
    target = logging.getLogger("evidence.jobs.purge")
    capture, previous = _Capture(), target.level
    target.addHandler(capture)
    target.setLevel(logging.INFO)
    yield capture.messages
    target.removeHandler(capture)
    target.setLevel(previous)


def _purge_audits(db_conn):
    return db_conn.execute(text("SELECT count(*) FROM access_audit WHERE action LIKE 'evidence.purge%'")).scalar_one()


# --- Eligibility --------------------------------------------------------------------------------------


def test_expired_retention_deletes_object_and_marks_purged_keeping_metadata(world, edata, db_conn):
    storage = FakeStorage()
    asset_id = _setup(db_conn, edata, storage, retention_until=PAST)
    before = _row(db_conn, asset_id)
    summary = _run(db_conn, storage)
    after = _row(db_conn, asset_id)
    assert summary["purged"] >= 1 and summary["failed"] == 0
    assert before["object_key"] not in storage.objects
    assert after["purged_at"] is not None and after["updated_at"] == after["purged_at"]
    for column in before:   # everything else is kept for traceability
        if column not in ("purged_at", "updated_at"):
            assert after[column] == before[column], column


def test_future_retention_is_not_purged(world, edata, db_conn):
    storage = FakeStorage()
    asset_id = _setup(db_conn, edata, storage, retention_until=FUTURE)
    _run(db_conn, storage)
    assert _row(db_conn, asset_id)["purged_at"] is None and _key(db_conn, asset_id) in storage.objects


def test_null_retention_without_request_is_not_purged(world, edata, db_conn):
    storage = FakeStorage()
    asset_id = _setup(db_conn, edata, storage, retention_until=None)
    _run(db_conn, storage)
    assert _row(db_conn, asset_id)["purged_at"] is None


def test_purge_request_purges_even_with_future_retention(world, edata, db_conn):
    storage = FakeStorage()
    asset_id = _setup(db_conn, edata, storage, retention_until=FUTURE, purge_requested_at=PAST)
    _run(db_conn, storage)
    row = _row(db_conn, asset_id)
    assert row["purged_at"] is not None and row["purge_requested_at"] is not None
    assert row["object_key"] not in storage.objects


def test_blocked_alone_is_not_purged(world, edata, db_conn):
    """a1_blocked has blocked_at set and a future retention: blocked ≠ purged."""
    storage = FakeStorage()
    asset_id = _setup(db_conn, edata, storage, "a1_blocked", retention_until=FUTURE)
    _run(db_conn, storage)
    assert _row(db_conn, asset_id)["purged_at"] is None and _key(db_conn, asset_id) in storage.objects


def test_blocked_and_expired_is_purged_keeping_blocked_at(world, edata, db_conn):
    storage = FakeStorage()
    asset_id = _setup(db_conn, edata, storage, "a1_blocked", retention_until=PAST)
    _run(db_conn, storage)
    row = _row(db_conn, asset_id)
    assert row["purged_at"] is not None and row["blocked_at"] is not None


def test_already_purged_is_not_processed(world, edata, db_conn):
    storage = FakeStorage()
    asset_id = edata.assets["a1_purged"]
    _set(db_conn, asset_id, retention_until=PAST)
    before = _row(db_conn, asset_id)
    _run(db_conn, storage)
    assert _row(db_conn, asset_id) == before
    assert not any(call.startswith("delete:") and before["object_key"] in call for call in storage.calls)


def test_non_clinical_and_other_bucket_rows_are_out_of_scope(world, edata, db_conn):
    """session_id NULL (DS03/MLOps) and rows of another bucket are never purged by Evidence."""
    storage = FakeStorage()
    orphan = edata.assets["orphan"]
    _set(db_conn, orphan, retention_until=PAST, purge_requested_at=PAST)
    other_bucket = add_asset(db_conn, edata.sessions["a1"], bucket="pef-models")
    _set(db_conn, other_bucket, retention_until=PAST)
    _run(db_conn, storage)
    assert _row(db_conn, orphan)["purged_at"] is None and _row(db_conn, other_bucket)["purged_at"] is None


# --- Idempotency and failures -----------------------------------------------------------------------------


def test_missing_object_is_still_marked_purged(world, edata, db_conn):
    storage = FakeStorage()
    asset_id = edata.assets["a1_gif"]
    _set(db_conn, asset_id, retention_until=PAST)    # no object in storage
    summary = _run(db_conn, storage)
    assert _row(db_conn, asset_id)["purged_at"] is not None and summary["failed"] == 0


def test_second_run_does_nothing(world, edata, db_conn):
    storage = FakeStorage()
    asset_id = _setup(db_conn, edata, storage, retention_until=PAST)
    _run(db_conn, storage)
    purged_at = _row(db_conn, asset_id)["purged_at"]
    calls = len(storage.calls)
    second = _run(db_conn, storage)
    assert _row(db_conn, asset_id)["purged_at"] == purged_at
    assert len([c for c in storage.calls[calls:] if c.startswith("delete:")]) == 0
    assert second["failed"] == 0


def test_delete_failure_keeps_asset_a_candidate(world, edata, db_conn, job_log):
    storage = FakeStorage(delete_error=True)
    asset_id = _setup(db_conn, edata, storage, retention_until=PAST)
    summary = _run(db_conn, storage, run_id="run-delete-fail")
    assert summary["failed"] >= 1 and _row(db_conn, asset_id)["purged_at"] is None
    assert any(f"asset={asset_id}" in m and "run=run-delete-fail" in m for m in job_log)
    storage.delete_error = False
    _run(db_conn, storage)                                        # next run completes it
    assert _row(db_conn, asset_id)["purged_at"] is not None


def test_db_failure_after_delete_is_completed_by_the_next_run(world, edata, db_conn, monkeypatch):
    storage = FakeStorage()
    asset_id = _setup(db_conn, edata, storage, retention_until=PAST)
    key = _key(db_conn, asset_id)
    original = maintenance_repository.mark_purged

    def failing(*args, **kwargs):
        raise IntegrityError("UPDATE media_asset ...", {}, Exception("injected"))
    monkeypatch.setattr(maintenance_repository, "mark_purged", failing)
    first = _run(db_conn, storage)
    assert first["failed"] >= 1
    assert key not in storage.objects                            # the blob is gone (cannot be restored)…
    assert _row(db_conn, asset_id)["purged_at"] is None          # …but the row was rolled back: still a candidate
    monkeypatch.setattr(maintenance_repository, "mark_purged", original)
    second = _run(db_conn, storage)
    assert second["failed"] == 0 and _row(db_conn, asset_id)["purged_at"] is not None   # idempotent DELETE + mark


def test_purge_never_deletes_rows_and_writes_no_audit(world, edata, db_conn):
    storage = FakeStorage()
    for key in ("a1_jpeg", "a1_gif", "a2"):
        _setup(db_conn, edata, storage, key, retention_until=PAST)
    rows_before = db_conn.execute(text("SELECT count(*) FROM media_asset")).scalar_one()
    audits_before = db_conn.execute(text("SELECT count(*) FROM access_audit")).scalar_one()
    _run(db_conn, storage)
    assert db_conn.execute(text("SELECT count(*) FROM media_asset")).scalar_one() == rows_before
    assert db_conn.execute(text("SELECT count(*) FROM access_audit")).scalar_one() == audits_before
    assert _purge_audits(db_conn) == 0     # no system actor exists: no fake evidence.purge row


# --- Dry run and batching -----------------------------------------------------------------------------------


def test_dry_run_changes_nothing(world, edata, db_conn, job_log):
    storage = FakeStorage()
    asset_id = _setup(db_conn, edata, storage, retention_until=PAST)
    requested = _setup(db_conn, edata, storage, "a1_gif", retention_until=FUTURE, purge_requested_at=PAST)
    before = (_row(db_conn, asset_id), _row(db_conn, requested))
    summary = _run(db_conn, storage, dry_run=True)
    assert summary["dry_run"] is True and summary["purged"] == 0 and summary["eligible"] >= 2
    assert (_row(db_conn, asset_id), _row(db_conn, requested)) == before
    assert not any(call.startswith("delete:") for call in storage.calls)
    messages = job_log
    assert any(f"asset={asset_id} reason=retention" in m for m in messages)
    assert any(f"asset={requested} reason=purge_requested" in m for m in messages)


def test_more_candidates_than_batch_size(world, edata, db_conn):
    storage = FakeStorage()
    session = edata.sessions["a1"]
    ids = [add_asset(db_conn, session, minute=10 + n) for n in range(5)]
    for asset_id in ids:
        _set(db_conn, asset_id, retention_until=PAST)
        storage.add_object(_key(db_conn, asset_id))
    summary = _run(db_conn, storage, batch_size=2)
    assert all(_row(db_conn, asset_id)["purged_at"] is not None for asset_id in ids)
    assert summary["scanned"] >= 5 and summary["purged"] >= 5


def test_candidate_scan_is_batched(world, edata, db_conn, monkeypatch):
    storage = FakeStorage()
    session = edata.sessions["a1"]
    for n in range(5):
        asset_id = add_asset(db_conn, session, minute=20 + n)
        _set(db_conn, asset_id, retention_until=PAST)
    sizes = []
    original = maintenance_repository.purge_candidates

    def spy(connection, bucket, now, after, limit):
        rows = original(connection, bucket, now, after, limit)
        sizes.append(len(rows))
        return rows
    monkeypatch.setattr(maintenance_repository, "purge_candidates", spy)
    _run(db_conn, storage, batch_size=2)
    assert max(sizes) <= 2 and len(sizes) >= 3


# --- Read API after purge ------------------------------------------------------------------------------------


def test_get_after_purge_shows_metadata_without_url(client, world, edata, db_conn, as_user, storage):
    asset_id = edata.assets["a1_jpeg"]
    _set(db_conn, asset_id, retention_until=PAST)
    storage.add_object(_key(db_conn, asset_id))
    _run(db_conn, storage)
    detail = client.get(f"/api/v1/evidence/{asset_id}", headers=as_user("op_a"))
    assert detail.status_code == 200
    body = detail.get_json()
    assert body["purged"] is True and body["signed_url"] is None and body["signed_url_expires_at"] is None
    listed = client.get(f"/api/v1/sessions/{edata.sessions['a1'].id}/evidence", headers=as_user("op_a")).get_json()
    assert str(asset_id) in [item["id"] for item in listed]
    assert db_conn.execute(text("SELECT count(*) FROM access_audit WHERE resource_id = :r"),
                           {"r": asset_id}).scalar_one() == 0


# --- CLI ---------------------------------------------------------------------------------------------------------


def test_cli_configuration_error_exits_2(monkeypatch, capsys):
    from config import ConfigError, Settings

    def broken():
        raise ConfigError("Missing required configuration: S3_ACCESS_KEY_ID")
    monkeypatch.setattr(Settings, "from_env", staticmethod(broken))
    assert purge.main([]) == EXIT_DEPENDENCY
    assert "S3_ACCESS_KEY_ID" in capsys.readouterr().err


def test_cli_storage_unavailable_exits_2_without_touching_anything(monkeypatch, pg_engine):
    from conftest import make_settings
    from config import Settings

    monkeypatch.setattr(Settings, "from_env", staticmethod(lambda: make_settings(s3_internal_endpoint="http://127.0.0.1:9")))
    assert purge.main(["--dry-run"]) == EXIT_DEPENDENCY


def test_exit_code_reflects_failures(world, edata, db_conn, monkeypatch, capsys):
    from config import Settings
    from conftest import make_settings
    import database as database_module
    import storage.s3_adapter as adapter_module

    storage = FakeStorage(delete_error=True)
    _setup(db_conn, edata, storage, retention_until=PAST)
    monkeypatch.setattr(Settings, "from_env", staticmethod(lambda: make_settings()))
    monkeypatch.setattr(database_module, "create_database", lambda settings: TransactionalDatabase(db_conn))
    monkeypatch.setattr(adapter_module, "S3Storage", lambda settings: storage)
    assert purge.main([]) == EXIT_FAILURES
    storage.delete_error = False
    assert purge.main([]) == EXIT_OK
    assert '"job": "purge"' in capsys.readouterr().out


def test_candidate_that_stops_being_one_before_the_lock_is_skipped(world, edata, db_conn, monkeypatch):
    """The scan saw a candidate; before the per-asset lock its retention was extended → nothing deleted."""
    storage = FakeStorage()
    asset_id = _setup(db_conn, edata, storage, retention_until=PAST)
    original = maintenance_repository.purge_candidates

    def scan_then_extend(connection, bucket, now, after, limit):
        rows = original(connection, bucket, now, after, limit)
        _set(db_conn, asset_id, retention_until=FUTURE)
        return rows
    monkeypatch.setattr(maintenance_repository, "purge_candidates", scan_then_extend)
    summary = _run(db_conn, storage)
    assert _key(db_conn, asset_id) in storage.objects and _row(db_conn, asset_id)["purged_at"] is None
    assert summary["skipped"] >= 1
