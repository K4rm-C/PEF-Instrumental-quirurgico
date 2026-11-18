"""Storage reconciliation job (Phase D): orphans, clinical-key filter, grace, delete flag, race re-check,
missing blobs, ordering, pagination, CLI. Runs are scoped to the test institution's key prefix."""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from conftest import FakeStorage, TransactionalDatabase
from evidence_fixtures import add_asset, edata  # noqa: F401  (fixture)
from jobs import reconcile
from jobs.common import EXIT_DEPENDENCY, EXIT_FAILURES, EXIT_OK
from repositories import maintenance_repository

OLD = 7200   # seconds; older than the default grace (3600)


def _clinical_key(world, session_id=None, asset_id=None, ext="jpg"):
    return f"{world.institutions['A']}/{session_id or uuid.uuid4()}/2026/{asset_id or uuid.uuid4()}.{ext}"


def _run(db_conn, storage, world, **kwargs):
    kwargs.setdefault("grace_seconds", 3600)
    kwargs.setdefault("batch_size", 100)
    kwargs.setdefault("prefix", f"{world.institutions['A']}/")
    return reconcile.run_reconcile(TransactionalDatabase(db_conn), storage, **kwargs)


def _asset_key(db_conn, asset_id):
    return db_conn.execute(text("SELECT object_key FROM media_asset WHERE id = :i"), {"i": asset_id}).scalar_one()


# --- Clinical key parser --------------------------------------------------------------------------------


@pytest.mark.parametrize("key, clinical", [
    (f"{uuid.uuid4()}/{uuid.uuid4()}/2026/{uuid.uuid4()}.jpg", True),
    (f"{uuid.uuid4()}/{uuid.uuid4()}/2026/{uuid.uuid4()}.gif", True),
    (f"{uuid.uuid4()}/{uuid.uuid4()}/2026/{uuid.uuid4()}.png", False),
    (f"{uuid.uuid4()}/{uuid.uuid4()}/2026/{uuid.uuid4()}.JPG", False),
    (f"{uuid.uuid4()}/{uuid.uuid4()}/26/{uuid.uuid4()}.jpg", False),
    (f"{uuid.uuid4()}/{uuid.uuid4()}/1999/{uuid.uuid4()}.jpg", False),
    (f"{str(uuid.uuid4()).upper()}/{uuid.uuid4()}/2026/{uuid.uuid4()}.jpg", False),
    (f"{uuid.uuid4()}/{uuid.uuid4()}/2026/{uuid.uuid4()}.jpg.bak", False),
    (f"x/{uuid.uuid4()}/{uuid.uuid4()}/2026/{uuid.uuid4()}.jpg", False),
    (f"{uuid.uuid4()}/{uuid.uuid4()}/2026/sub/{uuid.uuid4()}.jpg", False),
    ("models/yolo11n-pef-2026-03.pt", False),
    (f"models/{uuid.uuid4()}/2026/{uuid.uuid4()}.jpg", False),
    ("../../etc/passwd", False),
])
def test_clinical_key_format(key, clinical):
    assert reconcile.is_clinical_key(key) is clinical


# --- Orphans --------------------------------------------------------------------------------------------


def test_old_clinical_orphan_is_a_candidate_but_kept_without_flag(world, edata, db_conn):
    storage = FakeStorage()
    key = _clinical_key(world)
    storage.add_object(key, age_seconds=OLD)
    summary = _run(db_conn, storage, world)
    assert summary["orphan_candidates"] == 1 and summary["report"]["orphans"] == [key]
    assert summary["orphans_deleted"] == 0 and key in storage.objects


def test_young_clinical_orphan_is_ignored_by_grace(world, edata, db_conn):
    storage = FakeStorage()
    key = _clinical_key(world)
    storage.add_object(key, age_seconds=10)
    summary = _run(db_conn, storage, world, delete_orphans=True)
    assert summary["orphans_ignored_grace"] == 1 and summary["orphan_candidates"] == 0 and key in storage.objects


def test_delete_orphans_deletes_only_eligible_clinical_orphans(world, edata, db_conn):
    storage = FakeStorage()
    prefix = f"{world.institutions['A']}/"
    old, young = _clinical_key(world), _clinical_key(world)
    nonclinical = [f"{prefix}notes.txt", f"{prefix}{uuid.uuid4()}/2026/x.jpg", f"{prefix}{uuid.uuid4()}/2026/{uuid.uuid4()}.png"]
    storage.add_object(old, age_seconds=OLD)
    storage.add_object(young, age_seconds=5)
    for key in nonclinical:
        storage.add_object(key, age_seconds=OLD)
    referenced = edata.assets["a1_jpeg"]
    storage.add_object(_asset_key(db_conn, referenced), age_seconds=OLD)
    summary = _run(db_conn, storage, world, delete_orphans=True)
    assert summary["orphans_deleted"] == 1 and summary["report"]["deleted"] == [old]
    assert old not in storage.objects
    assert young in storage.objects and all(key in storage.objects for key in nonclinical)
    assert _asset_key(db_conn, referenced) in storage.objects
    assert summary["nonclinical_ignored"] == 3 and summary["orphans_ignored_grace"] == 1


def test_models_prefix_is_never_deleted(world, edata, db_conn):
    storage = FakeStorage()
    storage.add_object("models/yolo11n-pef-2026-03.pt", age_seconds=OLD)
    summary = _run(db_conn, storage, world, delete_orphans=True, prefix="")
    assert "models/yolo11n-pef-2026-03.pt" in storage.objects and summary["nonclinical_ignored"] >= 1


def test_dry_run_with_delete_flag_deletes_nothing(world, edata, db_conn):
    storage = FakeStorage()
    key = _clinical_key(world)
    storage.add_object(key, age_seconds=OLD)
    summary = _run(db_conn, storage, world, delete_orphans=True, dry_run=True)
    assert summary["orphan_candidates"] == 1 and summary["orphans_deleted"] == 0 and key in storage.objects


def test_object_with_row_is_not_an_orphan(world, edata, db_conn):
    storage = FakeStorage()
    for name in ("a1_jpeg", "a1_gif", "a2", "a1_blocked"):
        storage.add_object(_asset_key(db_conn, edata.assets[name]), age_seconds=OLD)
    summary = _run(db_conn, storage, world, delete_orphans=True, grace_seconds=0)
    assert summary["orphan_candidates"] == 0 and summary["orphans_deleted"] == 0


def test_row_appearing_before_the_delete_keeps_the_object(world, edata, db_conn, monkeypatch):
    """The scan saw no row; the upload commits before the delete → the re-check keeps the object."""
    storage = FakeStorage()
    asset_id = uuid.uuid4()
    session = edata.sessions["a1"]
    key = _clinical_key(world, session.id, asset_id)
    storage.add_object(key, age_seconds=OLD)
    original = maintenance_repository.iter_bucket_rows

    def rows_then_commit(database, bucket, prefix, batch_size):
        rows = list(original(database, bucket, prefix, batch_size))   # the scan's view: no row for key
        add_asset(db_conn, session, asset_id=asset_id, object_key=key)  # upload finishes now
        yield from rows
    monkeypatch.setattr(maintenance_repository, "iter_bucket_rows", rows_then_commit)
    summary = _run(db_conn, storage, world, delete_orphans=True, grace_seconds=0)
    assert key in storage.objects
    assert summary["orphans_kept_row_appeared"] == 1 and summary["orphans_deleted"] == 0


def test_orphan_delete_failure_is_counted(world, edata, db_conn):
    storage = FakeStorage(delete_error=True)
    storage.add_object(_clinical_key(world), age_seconds=OLD)
    summary = _run(db_conn, storage, world, delete_orphans=True)
    assert summary["failures"] == 1 and summary["orphans_deleted"] == 0


# --- Missing blobs ---------------------------------------------------------------------------------------


def _rows_snapshot(db_conn):
    return db_conn.execute(text("SELECT * FROM media_asset ORDER BY id")).mappings().all()


def test_missing_blob_detection_reports_only(world, edata, db_conn):
    storage = FakeStorage()
    present = edata.assets["a1_jpeg"]
    blocked_present = edata.assets["a1_blocked"]
    storage.add_object(_asset_key(db_conn, present), age_seconds=OLD)
    storage.add_object(_asset_key(db_conn, blocked_present), age_seconds=OLD)
    before = _rows_snapshot(db_conn)
    summary = _run(db_conn, storage, world, delete_orphans=True)
    missing = set(summary["report"]["missing"])
    # Non-purged rows without object: candidates (blocked included). Purged rows without object: correct.
    assert edata.assets["a1_gif"] in missing and edata.assets["a2"] in missing
    assert present not in missing and blocked_present not in missing
    assert edata.assets["a1_purged"] not in missing
    purged_key = _asset_key(db_conn, edata.assets["a1_purged"])
    assert f"exists:{purged_key}" not in storage.calls             # a purged row is not even re-checked
    assert summary["missing_blob_candidates"] == len(missing)
    assert _rows_snapshot(db_conn) == before                      # no metadata mutation
    assert len(storage.objects) == 2                               # nothing re-created


def test_blocked_without_object_is_a_missing_candidate(world, edata, db_conn):
    storage = FakeStorage()
    summary = _run(db_conn, storage, world)
    assert edata.assets["a1_blocked"] in summary["report"]["missing"]


def test_object_uploaded_after_the_listing_is_not_reported_missing(world, edata, db_conn):
    """The row's object is absent from the listing but exists when re-checked (HeadObject)."""

    class LateUpload(FakeStorage):
        def object_exists(self, object_key):
            return True
    summary = _run(db_conn, LateUpload(), world)
    assert summary["missing_blob_candidates"] == 0


def test_purged_row_with_object_present_is_reported(world, edata, db_conn):
    storage = FakeStorage()
    storage.add_object(_asset_key(db_conn, edata.assets["a1_purged"]), age_seconds=OLD)
    summary = _run(db_conn, storage, world)
    assert summary["purged_with_blob"] == 1


# --- Ordering, pagination, upload regression ------------------------------------------------------------------


def test_listing_out_of_order_aborts(world, edata, db_conn):
    class Unordered(FakeStorage):
        def iter_objects(self, prefix="", page_size=1000):
            yield from [("b", datetime.now(timezone.utc)), ("a", datetime.now(timezone.utc))]
    with pytest.raises(reconcile.ListingOrderError):
        _run(db_conn, Unordered(), world, prefix="")


def test_many_objects_and_rows_merge_correctly_with_small_batches(world, edata, db_conn):
    storage = FakeStorage()
    session = edata.sessions["a1"]
    referenced = []
    for n in range(7):
        asset_id = add_asset(db_conn, session, minute=30 + n)
        referenced.append(asset_id)
        storage.add_object(_asset_key(db_conn, asset_id), age_seconds=OLD)
    orphans = [_clinical_key(world, session.id) for _ in range(5)]
    for key in orphans:
        storage.add_object(key, age_seconds=OLD)
    summary = _run(db_conn, storage, world, batch_size=2)
    assert sorted(summary["report"]["orphans"]) == sorted(orphans)
    assert not set(referenced) & set(summary["report"]["missing"])


def test_paginated_listing(world):
    """The adapter follows ListObjectsV2 continuation tokens (page size → MaxKeys)."""
    import boto3
    from botocore.config import Config
    from botocore.stub import Stubber

    from conftest import make_settings
    from storage.s3_adapter import S3Storage

    client = boto3.client("s3", endpoint_url="http://storage.invalid:3900", region_name="garage",
                          aws_access_key_id="k", aws_secret_access_key="s", config=Config(s3={"addressing_style": "path"}))
    storage = S3Storage(make_settings(), client=client)
    now = datetime.now(timezone.utc)
    stubber = Stubber(client)
    stubber.add_response("list_objects_v2", {"Contents": [{"Key": "a", "LastModified": now}, {"Key": "b", "LastModified": now}],
                                             "IsTruncated": True, "NextContinuationToken": "t1"},
                         {"Bucket": "evidence", "Prefix": "", "MaxKeys": 2})
    stubber.add_response("list_objects_v2", {"Contents": [{"Key": "c", "LastModified": now}], "IsTruncated": False},
                         {"Bucket": "evidence", "Prefix": "", "MaxKeys": 2, "ContinuationToken": "t1"})
    with stubber:
        assert [key for key, _ in storage.iter_objects(page_size=2)] == ["a", "b", "c"]
    stubber.assert_no_pending_responses()


def test_fresh_upload_is_not_treated_as_orphan(client, world, edata, db_conn, as_user, storage):
    response = client.post("/api/v1/evidence", content_type="multipart/form-data", headers=as_user("op_a"),
                           data={"session_id": str(edata.sessions["a1"].id), "kind": "jpeg",
                                 "file": (io.BytesIO(b"\xff\xd8\xff\xe0" + b"x" * 100), "x.jpg")})
    assert response.status_code == 201
    key = _asset_key(db_conn, response.get_json()["id"])
    summary = _run(db_conn, storage, world, delete_orphans=True, grace_seconds=0)
    assert key in storage.objects and key not in summary["report"]["orphans"]


def test_upload_in_flight_is_protected_by_grace(client, world, edata, db_conn, as_user, storage):
    """Reconcile runs between the upload's PUT and its INSERT: the object is too young to be touched."""
    results = []
    storage.on_put = lambda: results.append(_run(db_conn, storage, world, delete_orphans=True, grace_seconds=3600))
    response = client.post("/api/v1/evidence", content_type="multipart/form-data", headers=as_user("op_a"),
                           data={"session_id": str(edata.sessions["a1"].id), "kind": "jpeg",
                                 "file": (io.BytesIO(b"\xff\xd8\xff\xe0" + b"y" * 100), "y.jpg")})
    assert response.status_code == 201
    assert results[0]["orphans_ignored_grace"] == 1 and results[0]["orphans_deleted"] == 0


# --- CLI -------------------------------------------------------------------------------------------------------


def test_cli_exit_codes(world, edata, db_conn, monkeypatch, capsys):
    from config import ConfigError, Settings
    from conftest import make_settings
    import database as database_module
    import storage.s3_adapter as adapter_module

    def broken():
        raise ConfigError("Missing required configuration: S3_BUCKET_EVIDENCE")
    monkeypatch.setattr(Settings, "from_env", staticmethod(broken))
    assert reconcile.main([]) == EXIT_DEPENDENCY

    storage = FakeStorage()
    monkeypatch.setattr(Settings, "from_env", staticmethod(lambda: make_settings()))
    monkeypatch.setattr(database_module, "create_database", lambda settings: TransactionalDatabase(db_conn))
    monkeypatch.setattr(adapter_module, "S3Storage", lambda settings: storage)
    assert reconcile.main(["--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert '"job": "reconcile"' in out and "report" not in out
    storage.delete_error = True
    storage.add_object(_clinical_key(world), age_seconds=OLD)
    assert reconcile.main(["--delete-orphans"]) == EXIT_FAILURES
