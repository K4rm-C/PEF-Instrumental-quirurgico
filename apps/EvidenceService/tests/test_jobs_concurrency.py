"""Two purge workers on the same asset — real PostgreSQL row locks (committed data, independent connections).

A dedicated bucket name keeps the committed rows out of every other run; everything is deleted at teardown.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from conftest import FakeStorage, make_settings
from database import create_database
from jobs import purge

HOLD_SECONDS = 0.5


class SlowDeleteStorage(FakeStorage):
    """DeleteObject takes HOLD_SECONDS so the first worker holds the row lock while the second one runs."""

    def __init__(self, bucket):
        super().__init__()
        self.bucket = bucket
        self.lock = threading.Lock()

    def delete_object(self, object_key):
        time.sleep(HOLD_SECONDS)
        with self.lock:
            super().delete_object(object_key)


@pytest.fixture
def committed_asset(pg_engine):
    tag = uuid.uuid4().hex[:8]
    bucket = f"pytest-purge-{tag}"
    institution, user, operation, session, asset = (uuid.uuid4() for _ in range(5))
    key = f"{institution}/{session}/2026/{asset}.jpg"
    with pg_engine.begin() as c:
        c.execute(text("INSERT INTO institution (id, name) VALUES (:i, :n)"), {"i": institution, "n": f"Purge {tag}"})
        c.execute(text('INSERT INTO "user" (id, name, email, password_hash, institution_id) VALUES '
                       "(:u, 'Purge', :e, 'x', :i)"), {"u": user, "e": f"purge.{tag}@evidence-test.invalid", "i": institution})
        c.execute(text("INSERT INTO operation (id, status_id, institution_id) VALUES "
                       "(:o, (SELECT id FROM cat_operation_status WHERE code = 'scheduled'), :i)"), {"o": operation, "i": institution})
        c.execute(text("INSERT INTO work_session (id, status_id, user_id, operation_id, capture_mode) VALUES "
                       "(:s, (SELECT id FROM cat_session_status WHERE code = 'closed'), :u, :o, 'vision')"),
                  {"s": session, "u": user, "o": operation})
        c.execute(text("INSERT INTO media_asset (id, session_id, gcs_uri, bucket, object_key, content_type, kind, "
                       "storage_provider, retention_until) VALUES (:a, :s, :uri, :b, :k, 'image/jpeg', 'jpeg', 'other', :r)"),
                  {"a": asset, "s": session, "uri": f"s3://{bucket}/{key}", "b": bucket, "k": key,
                   "r": datetime.now(timezone.utc) - timedelta(days=1)})
    yield bucket, asset, key
    with pg_engine.begin() as c:
        c.execute(text("DELETE FROM media_asset WHERE id = :a"), {"a": asset})
        c.execute(text("DELETE FROM work_session WHERE id = :s"), {"s": session})
        c.execute(text("DELETE FROM operation WHERE id = :o"), {"o": operation})
        c.execute(text('DELETE FROM "user" WHERE id = :u'), {"u": user})
        c.execute(text("DELETE FROM institution WHERE id = :i"), {"i": institution})


def test_two_purge_workers_purge_once(committed_asset, pg_engine):
    """Worker A locks the row and deletes slowly; worker B starts 0.1 s later and must SKIP the locked row
    (not wait for it): exactly one delete, purged_at set once."""
    bucket, asset, key = committed_asset
    storage = SlowDeleteStorage(bucket)
    storage.add_object(key)
    database = create_database(make_settings())
    results = {}

    def worker(name, delay):
        time.sleep(delay)
        started = time.perf_counter()
        summary = purge.run_purge(database, storage, batch_size=10)
        results[name] = (summary, time.perf_counter() - started)

    threads = [threading.Thread(target=worker, args=("A", 0)), threading.Thread(target=worker, args=("B", 0.1))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    database.engine.dispose()

    (summary_a, _), (summary_b, elapsed_b) = results["A"], results["B"]
    assert (summary_a["purged"], summary_b["purged"]) == (1, 0)
    assert summary_b["skipped"] == 1 and summary_a["failed"] == summary_b["failed"] == 0
    assert elapsed_b < HOLD_SECONDS * 0.5, elapsed_b                   # skipped the locked row, did not wait
    assert [c for c in storage.calls if c.startswith("delete:")] == [f"delete:{key}"]   # one delete only
    assert key not in storage.objects
    with pg_engine.connect() as c:
        purged_at = c.execute(text("SELECT purged_at FROM media_asset WHERE id = :a"), {"a": asset}).scalar_one()
    assert purged_at is not None
