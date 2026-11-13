"""S3-compatible object storage adapter (Garage today; PEF-DS05).

- ``check_bucket`` (Phase A, /health/storage): one ``HeadBucket`` on the evidence bucket through the
  INTERNAL endpoint.
- ``generate_presigned_get`` (Phase B, GET /evidence/{id}): a SigV4 presigned GET signed directly
  against the PUBLIC endpoint (PEF-DS05 §1.3.2 rule 11) — never signed for the internal host and
  rewritten afterwards. Presigning is a local computation: no request reaches the storage.

- ``put_file`` / ``head_object_size`` / ``delete_object`` (Phase C, POST /evidence): upload of the
  validated temporary file, size verification and compensating delete, all through the INTERNAL
  endpoint. No user metadata is attached to objects (no filename, correlation id, token or PII).

- ``iter_objects`` / ``object_exists`` (Phase D, internal jobs): paginated ListObjectsV2 of the
  evidence bucket (keys in ascending order, as S3 returns them) and an existence check (HeadObject);
  ``delete_object`` is also the purge job's delete. Jobs only use the INTERNAL endpoint.

It never creates buckets.

Garage specifics: path-style addressing, SigV4 with the configured region (Garage's ``s3_region``),
and botocore's optional request/response checksums only when the operation requires them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import BinaryIO, Iterator

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from config import Settings

logger = logging.getLogger("evidence.storage")

_MISSING_BUCKET_CODES = {"404", "NoSuchBucket", "NotFound"}
_MISSING_OBJECT_CODES = {"404", "NoSuchKey", "NotFound"}
_DENIED_CODES = {"403", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "Forbidden"}


class StorageUnavailable(Exception):
    """The evidence bucket cannot be used. ``reason`` is a fixed category, never a raw storage message."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason   # unreachable | access_denied | bucket_missing | error


class StorageSigningError(Exception):
    """A presigned URL could not be produced (no raw storage/botocore detail is kept)."""


class StorageOperationError(Exception):
    """An object operation (PUT/HEAD/DELETE) failed (no raw storage/botocore detail is kept)."""


def _client(settings: Settings, endpoint_url: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=settings.dependency_timeout_seconds,
            read_timeout=settings.dependency_timeout_seconds,
            retries={"max_attempts": 1, "mode": "standard"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


class S3Storage:
    def __init__(self, settings: Settings, client=None, signer=None):
        self.bucket = settings.s3_bucket_evidence
        self._client = client or _client(settings, settings.s3_internal_endpoint)   # storage operations
        self._signer = signer or _client(settings, settings.s3_public_endpoint)     # URLs for browsers/clients

    def check_bucket(self) -> None:
        """Endpoint reachable, credentials accepted and bucket present; read-only (HeadBucket)."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = str(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
            if code in _MISSING_BUCKET_CODES or status == "404":
                raise StorageUnavailable("bucket_missing") from None
            if code in _DENIED_CODES or status == "403":
                raise StorageUnavailable("access_denied") from None
            raise StorageUnavailable("error") from None
        except BotoCoreError:
            raise StorageUnavailable("unreachable") from None

    def generate_presigned_get(self, bucket: str, object_key: str, expires_in: int) -> str:
        """SigV4 presigned GET for (bucket, object_key), valid ``expires_in`` seconds, on the PUBLIC endpoint."""
        try:
            return self._signer.generate_presigned_url(
                "get_object", Params={"Bucket": bucket, "Key": object_key}, ExpiresIn=expires_in
            )
        except Exception:
            raise StorageSigningError() from None

    def put_file(self, object_key: str, fileobj: BinaryIO, size: int, content_type: str) -> None:
        """PUT the (already validated) file object into the evidence bucket. Only Content-Type is set."""
        try:
            self._client.put_object(Bucket=self.bucket, Key=object_key, Body=fileobj, ContentLength=size,
                                    ContentType=content_type)
        except (BotoCoreError, ClientError):
            raise StorageOperationError("put") from None

    def head_object_size(self, object_key: str) -> int:
        """ContentLength stored for the object (HeadObject). ETag is NOT a SHA-256 and is not used."""
        try:
            return int(self._client.head_object(Bucket=self.bucket, Key=object_key)["ContentLength"])
        except (BotoCoreError, ClientError, KeyError, TypeError, ValueError):
            raise StorageOperationError("head") from None

    def delete_object(self, object_key: str) -> None:
        """DeleteObject. Deleting a key that does not exist succeeds (S3 semantics): retry-safe."""
        try:
            self._client.delete_object(Bucket=self.bucket, Key=object_key)
        except (BotoCoreError, ClientError):
            raise StorageOperationError("delete") from None

    def iter_objects(self, prefix: str = "", page_size: int = 1000) -> Iterator[tuple[str, datetime]]:
        """(key, LastModified UTC) of every object under ``prefix``, page by page (never the whole bucket at once)."""
        paginator = self._client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix, PaginationConfig={"PageSize": page_size}):
                for item in page.get("Contents", []):
                    yield item["Key"], item["LastModified"].astimezone(timezone.utc)
        except (BotoCoreError, ClientError):
            raise StorageOperationError("list") from None

    def object_exists(self, object_key: str) -> bool:
        """HeadObject: False only for "not found"; any other failure raises StorageOperationError."""
        try:
            self._client.head_object(Bucket=self.bucket, Key=object_key)
            return True
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = str(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
            if code in _MISSING_OBJECT_CODES or status == "404":
                return False
            raise StorageOperationError("head") from None
        except BotoCoreError:
            raise StorageOperationError("head") from None
