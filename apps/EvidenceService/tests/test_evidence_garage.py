"""Optional real integration: Evidence API → presigned URL → Garage (skipped if Garage is not usable).

A temporary, non-clinical object is written to the evidence bucket BY THE TEST (the service has no
write path), the media_asset rows live in the rolled-back transaction, and the object is deleted at
teardown.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
import uuid

import boto3
import pytest
from botocore.config import Config

from conftest import ROOT_ENV, TransactionalDatabase, make_settings
from evidence_fixtures import add_asset, add_session, audits
from main import create_app
from storage.s3_adapter import S3Storage, StorageUnavailable

PAYLOAD = b"\xff\xd8\xff\xe0PEF-EVIDENCE-TEST-NOT-CLINICAL-" + os.urandom(64) + b"\xff\xd9"


@pytest.fixture
def garage():
    if not (ROOT_ENV.get("S3_ACCESS_KEY") and ROOT_ENV.get("S3_SECRET_KEY")):
        pytest.skip("Garage credentials not available in the repository .env")
    endpoint = f"http://localhost:{ROOT_ENV.get('S3_API_PORT', '3900')}"
    settings = make_settings(
        s3_internal_endpoint=endpoint, s3_public_endpoint=endpoint, s3_region=ROOT_ENV.get("S3_REGION", "garage"),
        s3_access_key_id=ROOT_ENV["S3_ACCESS_KEY"], s3_secret_access_key=ROOT_ENV["S3_SECRET_KEY"],
        s3_bucket_evidence=ROOT_ENV.get("S3_BUCKET_EVIDENCE", "evidence"),
    )
    storage = S3Storage(settings)
    try:
        storage.check_bucket()
    except StorageUnavailable as exc:
        pytest.skip(f"Garage not usable for integration tests: {exc.reason}")
    client = boto3.client("s3", endpoint_url=endpoint, region_name=settings.s3_region,
                          aws_access_key_id=settings.s3_access_key_id,
                          aws_secret_access_key=settings.s3_secret_access_key,
                          config=Config(s3={"addressing_style": "path"}, request_checksum_calculation="when_required",
                                        response_checksum_validation="when_required"))
    keys = []

    def put(key):
        client.put_object(Bucket=settings.s3_bucket_evidence, Key=key, Body=PAYLOAD, ContentType="image/jpeg")
        keys.append(key)
        return key

    yield settings, storage, client, put
    for key in keys:
        client.delete_object(Bucket=settings.s3_bucket_evidence, Key=key)


def _fetch(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read()


def _setup(db_conn, redis_client, world, garage, **asset_flags):
    settings, storage, client, put = garage
    session = add_session(db_conn, world, "op_a", "A")
    asset_id = uuid.uuid4()
    key = put(f"{world.institutions['A']}/{session.id}/2026/{asset_id}.jpg")
    add_asset(db_conn, session, asset_id=asset_id, object_key=key, bucket=settings.s3_bucket_evidence, **asset_flags)
    app = create_app(settings, database=TransactionalDatabase(db_conn), redis_client=redis_client, storage=storage)
    return app.test_client(), asset_id, key, client, settings


def test_signed_url_downloads_the_exact_bytes(db_conn, redis_client, world, tokens, garage):
    client, asset_id, _, _, _ = _setup(db_conn, redis_client, world, garage)
    user = world.users["op_a"]
    body = client.get(f"/api/v1/evidence/{asset_id}",
                      headers={"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id)}"}).get_json()
    assert body["signed_url"] is not None
    status, content = _fetch(body["signed_url"])
    assert status == 200 and content == PAYLOAD
    assert len(audits(db_conn, asset_id)) == 1


def test_tampered_signed_url_is_rejected_by_garage(db_conn, redis_client, world, tokens, garage):
    client, asset_id, _, _, _ = _setup(db_conn, redis_client, world, garage)
    user = world.users["spd_a"]
    url = client.get(f"/api/v1/evidence/{asset_id}",
                     headers={"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id)}"}).get_json()["signed_url"]
    with pytest.raises(urllib.error.HTTPError) as info:
        _fetch(url.replace("X-Amz-Expires=300", "X-Amz-Expires=3000"))
    assert info.value.code == 403


@pytest.mark.parametrize("flags", [{"blocked": True}, {"purged": True}])
def test_blocked_or_purged_gets_no_url_while_object_still_exists(db_conn, redis_client, world, tokens, garage, flags):
    client, asset_id, key, s3, settings = _setup(db_conn, redis_client, world, garage, **flags)
    user = world.users["spd_a"]
    body = client.get(f"/api/v1/evidence/{asset_id}",
                      headers={"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id)}"}).get_json()
    assert body["signed_url"] is None and body["signed_url_expires_at"] is None
    assert audits(db_conn, asset_id) == []
    s3.head_object(Bucket=settings.s3_bucket_evidence, Key=key)      # test-side check: blocked/purged flag ≠ object gone


# --- Phase C: real upload through the API ------------------------------------------------------------------


def test_upload_then_signed_download_returns_the_exact_bytes(db_conn, redis_client, world, tokens, garage):
    import hashlib
    import io

    from sqlalchemy import text

    settings, storage, s3, _ = garage
    session = add_session(db_conn, world, "op_a", "A")
    app = create_app(settings, database=TransactionalDatabase(db_conn), redis_client=redis_client, storage=storage)
    client = app.test_client()
    user = world.users["op_a"]
    headers = {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id)}"}
    response = client.post("/api/v1/evidence", content_type="multipart/form-data", headers=headers,
                           data={"session_id": str(session.id), "kind": "jpeg", "file": (io.BytesIO(PAYLOAD), "x.jpg")})
    assert response.status_code == 201, response.get_data(as_text=True)
    asset_id = response.get_json()["id"]
    key = db_conn.execute(text("SELECT object_key FROM media_asset WHERE id = :i"), {"i": asset_id}).scalar_one()
    try:
        head = s3.head_object(Bucket=settings.s3_bucket_evidence, Key=key)
        assert head["ContentLength"] == len(PAYLOAD) and head["ContentType"] == "image/jpeg"
        assert head.get("Metadata", {}) == {}                                        # no user metadata on the object
        assert response.get_json()["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
        detail = client.get(f"/api/v1/evidence/{asset_id}", headers=headers).get_json()
        status, content = _fetch(detail["signed_url"])
        assert status == 200 and content == PAYLOAD
        assert hashlib.sha256(content).hexdigest() == detail["sha256"]
    finally:
        s3.delete_object(Bucket=settings.s3_bucket_evidence, Key=key)


# --- Phase D: jobs against the real Garage ----------------------------------------------------------------


def _exists(client, settings, key):
    from botocore.exceptions import ClientError

    try:
        client.head_object(Bucket=settings.s3_bucket_evidence, Key=key)
        return True
    except ClientError:
        return False


def test_delete_of_a_missing_object_succeeds_on_garage(garage):
    _, storage, _, _ = garage
    storage.delete_object(f"pytest-missing/{uuid.uuid4()}.jpg")    # idempotent: no error


def test_purge_job_deletes_the_object_and_keeps_the_row(db_conn, redis_client, world, tokens, garage):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    from jobs import purge

    client, asset_id, key, s3, settings = _setup(db_conn, redis_client, world, garage)
    db_conn.execute(text("UPDATE media_asset SET retention_until = :r WHERE id = :i"),
                    {"r": datetime.now(timezone.utc) - timedelta(days=1), "i": asset_id})
    _, storage, _, _ = garage
    summary = purge.run_purge(TransactionalDatabase(db_conn), storage, batch_size=100)
    assert summary["failed"] == 0 and summary["purged"] >= 1
    assert not _exists(s3, settings, key)
    row = db_conn.execute(text("SELECT purged_at, sha256, object_key FROM media_asset WHERE id = :i"),
                          {"i": asset_id}).mappings().one()
    assert row["purged_at"] is not None and row["object_key"] == key
    user = world.users["op_a"]
    body = client.get(f"/api/v1/evidence/{asset_id}",
                      headers={"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id)}"}).get_json()
    assert body["purged"] is True and body["signed_url"] is None
    assert audits(db_conn, asset_id) == []


def test_reconcile_job_on_garage(db_conn, world, garage):
    from jobs import reconcile

    settings, storage, s3, put = garage
    session = add_session(db_conn, world, "op_a", "A")
    prefix = f"{world.institutions['A']}/"
    referenced_id = uuid.uuid4()
    referenced = put(f"{prefix}{session.id}/2026/{referenced_id}.jpg")
    add_asset(db_conn, session, asset_id=referenced_id, object_key=referenced, bucket=settings.s3_bucket_evidence)
    orphan = put(f"{prefix}{session.id}/2026/{uuid.uuid4()}.jpg")
    nonclinical = put(f"{prefix}notes/readme.txt")
    missing_id = add_asset(db_conn, session, bucket=settings.s3_bucket_evidence)   # row without object

    database = TransactionalDatabase(db_conn)
    young = reconcile.run_reconcile(database, storage, grace_seconds=3600, batch_size=100, delete_orphans=True,
                                    prefix=prefix, page_size=1)                    # page_size=1: real pagination
    assert young["orphans_ignored_grace"] == 1 and young["orphans_deleted"] == 0 and _exists(s3, settings, orphan)
    assert young["objects_scanned"] == 3 and young["nonclinical_ignored"] == 1
    assert young["report"]["missing"] == [missing_id]

    report_only = reconcile.run_reconcile(database, storage, grace_seconds=0, batch_size=100, prefix=prefix)
    assert report_only["report"]["orphans"] == [orphan] and _exists(s3, settings, orphan)

    cleanup = reconcile.run_reconcile(database, storage, grace_seconds=0, batch_size=100, delete_orphans=True,
                                      prefix=prefix)
    assert cleanup["report"]["deleted"] == [orphan] and cleanup["failures"] == 0
    assert not _exists(s3, settings, orphan)
    assert _exists(s3, settings, referenced) and _exists(s3, settings, nonclinical)
