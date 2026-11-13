"""S3 adapter (HeadBucket only) — stubbed botocore client, plus optional real-Garage integration."""

from __future__ import annotations

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import EndpointConnectionError, ReadTimeoutError
from botocore.stub import Stubber

from conftest import ROOT_ENV, FakeStorage, TransactionalDatabase, make_settings
from main import create_app
from storage.s3_adapter import S3Storage, StorageUnavailable


def _stubbed(**overrides):
    settings = make_settings(**overrides)
    client = boto3.client("s3", endpoint_url="http://storage.invalid:3900", region_name="garage",
                          aws_access_key_id="k", aws_secret_access_key="s",
                          config=Config(s3={"addressing_style": "path"}))
    return S3Storage(settings, client=client), Stubber(client)


def test_bucket_reachable():
    storage, stubber = _stubbed()
    stubber.add_response("head_bucket", {}, {"Bucket": "evidence"})
    with stubber:
        storage.check_bucket()
    stubber.assert_no_pending_responses()   # exactly one HeadBucket, nothing else


@pytest.mark.parametrize("code, status, reason", [
    ("404", 404, "bucket_missing"), ("NoSuchBucket", 404, "bucket_missing"),
    ("403", 403, "access_denied"), ("InvalidAccessKeyId", 403, "access_denied"),
    ("SignatureDoesNotMatch", 403, "access_denied"), ("InternalError", 500, "error"),
])
def test_client_errors_map_to_fixed_reasons(code, status, reason):
    storage, stubber = _stubbed()
    stubber.add_client_error("head_bucket", service_error_code=code, service_message="raw garage message",
                             http_status_code=status, expected_params={"Bucket": "evidence"})
    with stubber, pytest.raises(StorageUnavailable) as info:
        storage.check_bucket()
    assert info.value.reason == reason and "raw garage message" not in str(info.value)


@pytest.mark.parametrize("error", [EndpointConnectionError(endpoint_url="http://storage.invalid:3900"),
                                   ReadTimeoutError(endpoint_url="http://storage.invalid:3900")])
def test_unreachable_endpoint(error):
    class Failing:
        def head_bucket(self, **kwargs):
            raise error
    storage = S3Storage(make_settings(), client=Failing())
    with pytest.raises(StorageUnavailable) as info:
        storage.check_bucket()
    assert info.value.reason == "unreachable" and "storage.invalid" not in str(info.value)


def test_adapter_is_configured_for_garage():
    storage = S3Storage(make_settings(s3_internal_endpoint="http://garage:3900", s3_public_endpoint="http://pub:3900"))
    for client, endpoint in ((storage._client, "http://garage:3900"), (storage._signer, "http://pub:3900")):
        assert client.meta.endpoint_url == endpoint                  # internal for operations, public for URLs
        assert client.meta.region_name == "garage"
        assert client.meta.config.s3["addressing_style"] == "path"
        assert client.meta.config.signature_version == "s3v4"


def test_adapter_operations_are_exactly_the_approved_ones():
    public = {name for name in dir(S3Storage) if not name.startswith("_")}
    assert public == {"check_bucket", "generate_presigned_get", "put_file", "head_object_size", "delete_object",
                      "iter_objects", "object_exists"}


# --- Optional integration against the real Garage of docker compose ---------------------------------


def _real_settings():
    if not (ROOT_ENV.get("S3_ACCESS_KEY") and ROOT_ENV.get("S3_SECRET_KEY")):
        pytest.skip("Garage credentials not available in the repository .env")
    return make_settings(
        s3_internal_endpoint=f"http://localhost:{ROOT_ENV.get('S3_API_PORT', '3900')}",
        s3_region=ROOT_ENV.get("S3_REGION", "garage"),
        s3_access_key_id=ROOT_ENV["S3_ACCESS_KEY"],
        s3_secret_access_key=ROOT_ENV["S3_SECRET_KEY"],
        s3_bucket_evidence=ROOT_ENV.get("S3_BUCKET_EVIDENCE", "evidence"),
    )


@pytest.fixture
def real_storage():
    settings = _real_settings()
    storage = S3Storage(settings)
    try:
        storage.check_bucket()
    except StorageUnavailable as exc:
        pytest.skip(f"Garage not usable for integration tests: {exc.reason}")
    return settings, storage


def _bucket_snapshot(settings):
    client = boto3.client("s3", endpoint_url=settings.s3_internal_endpoint, region_name=settings.s3_region,
                          aws_access_key_id=settings.s3_access_key_id,
                          aws_secret_access_key=settings.s3_secret_access_key,
                          config=Config(s3={"addressing_style": "path"}))
    buckets = sorted(b["Name"] for b in client.list_buckets()["Buckets"])
    objects = client.list_objects_v2(Bucket=settings.s3_bucket_evidence).get("KeyCount", 0)
    return buckets, objects


def test_real_garage_health_has_no_side_effects(real_storage, db_conn, redis_client):
    settings, storage = real_storage
    before = _bucket_snapshot(settings)
    app = create_app(settings, database=TransactionalDatabase(db_conn), redis_client=redis_client, storage=storage)
    client = app.test_client()
    for path in ("/health/storage", "/health/ready", "/health/storage"):
        assert client.get(path).status_code == 200, path
    assert _bucket_snapshot(settings) == before                 # no bucket or object created


def test_real_garage_missing_bucket_is_not_created(real_storage):
    settings, _ = real_storage
    from dataclasses import replace

    missing = replace(settings, s3_bucket_evidence="pef-evidence-does-not-exist")
    with pytest.raises(StorageUnavailable) as info:
        S3Storage(missing).check_bucket()
    assert info.value.reason == "bucket_missing"
    assert "pef-evidence-does-not-exist" not in _bucket_snapshot(settings)[0]


def test_real_garage_bad_credentials(real_storage):
    settings, _ = real_storage
    from dataclasses import replace

    with pytest.raises(StorageUnavailable) as info:
        S3Storage(replace(settings, s3_secret_access_key="wrong-secret")).check_bucket()
    assert info.value.reason == "access_denied"


def test_fake_storage_is_isolated():
    assert FakeStorage().calls == []


# --- Phase C object operations (internal endpoint) --------------------------------------------------------


def test_put_head_delete_use_the_evidence_bucket_and_no_metadata():
    import io

    from botocore.stub import ANY

    storage, stubber = _stubbed()
    body = io.BytesIO(b"\xff\xd8\xff" + b"x" * 10)
    stubber.add_response("put_object", {}, {"Bucket": "evidence", "Key": "i/s/2026/a.jpg", "Body": ANY,
                                            "ContentLength": 13, "ContentType": "image/jpeg"})   # exactly these params
    stubber.add_response("head_object", {"ContentLength": 13}, {"Bucket": "evidence", "Key": "i/s/2026/a.jpg"})
    stubber.add_response("delete_object", {}, {"Bucket": "evidence", "Key": "i/s/2026/a.jpg"})
    with stubber:
        storage.put_file("i/s/2026/a.jpg", body, 13, "image/jpeg")
        assert storage.head_object_size("i/s/2026/a.jpg") == 13
        storage.delete_object("i/s/2026/a.jpg")
    stubber.assert_no_pending_responses()


@pytest.mark.parametrize("operation", ["put_object", "head_object", "delete_object"])
def test_object_operation_errors_are_wrapped(operation):
    import io

    from storage.s3_adapter import StorageOperationError

    storage, stubber = _stubbed()
    stubber.add_client_error(operation, service_error_code="InternalError", service_message="raw garage text",
                             http_status_code=500)
    calls = {"put_object": lambda: storage.put_file("k", io.BytesIO(b"x"), 1, "image/jpeg"),
             "head_object": lambda: storage.head_object_size("k"),
             "delete_object": lambda: storage.delete_object("k")}
    with stubber, pytest.raises(StorageOperationError) as info:
        calls[operation]()
    assert "raw garage text" not in str(info.value)


def test_object_operations_use_the_internal_endpoint_and_signing_the_public_one():
    storage = S3Storage(make_settings(s3_internal_endpoint="http://garage:3900", s3_public_endpoint="http://pub:3900"))
    sent = []
    storage._client.meta.events.register("before-send", lambda request, **kw: sent.append(request.url) or _Fake200())
    storage.delete_object("i/s/2026/a.jpg")
    assert sent and sent[0].startswith("http://garage:3900/evidence/i/s/2026/a.jpg")
    assert storage.generate_presigned_get("evidence", "k.jpg", 300).startswith("http://pub:3900/evidence/k.jpg")


class _Fake200:
    """Minimal botocore HTTP response so no real network call is made."""
    status_code = 204
    headers = {}
    content = b""
    raw = None

    @property
    def text(self):
        return ""
