"""S3Storage.generate_presigned_get: public endpoint, SigV4, path-style, TTL, and no network call."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from conftest import make_settings
from storage.s3_adapter import S3Storage, StorageSigningError

KEY = "11111111-1111-1111-1111-111111111111/cccccccc-cccc-cccc-cccc-cccccccccccc/2026/frame 1+a.jpg"


def _storage(**overrides):
    settings = make_settings(s3_internal_endpoint="http://garage:3900",
                             s3_public_endpoint="https://public.example:8443", **overrides)
    return S3Storage(settings)


def test_url_uses_public_endpoint_path_style_and_sigv4():
    url = _storage().generate_presigned_get("evidence", KEY, 300)
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.hostname, parsed.port) == ("https", "public.example", 8443)
    assert parsed.path.startswith("/evidence/11111111-1111-1111-1111-111111111111/")   # path-style bucket
    assert "garage" not in parsed.netloc                                                # never the internal host
    query = parse_qs(parsed.query)
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Expires"] == ["300"]
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert query["X-Amz-Credential"][0].endswith("/garage/s3/aws4_request")              # region garage
    assert len(query["X-Amz-Signature"][0]) == 64


def test_ttl_is_applied():
    query = parse_qs(urlparse(_storage().generate_presigned_get("evidence", "k.jpg", 45)).query)
    assert query["X-Amz-Expires"] == ["45"]


def test_presign_makes_no_network_call():
    """Any HTTP request (HeadObject, GetObject, ListObjects, ...) would go through before-send: none happens."""
    storage = _storage()
    sent = []

    def forbid(**kwargs):
        sent.append(kwargs.get("request"))
        raise AssertionError("network call during presign")
    for client in (storage._signer, storage._client):
        client.meta.events.register("before-send", forbid)
    storage.generate_presigned_get("evidence", KEY, 300)
    assert sent == []


def test_signer_failure_is_wrapped_without_details():
    class Exploding:
        def generate_presigned_url(self, *args, **kwargs):
            raise RuntimeError("raw botocore detail with secret-key")
    storage = S3Storage(make_settings(), client=object(), signer=Exploding())
    with pytest.raises(StorageSigningError) as info:
        storage.generate_presigned_get("evidence", "k.jpg", 300)
    assert "secret" not in str(info.value)
