"""Evidence Service configuration, read exclusively from environment variables.

Required (no defaults, the service refuses to start without them): PostgreSQL, Redis, JWT secret,
both S3 endpoints, S3 region, S3 credentials and the evidence bucket. Error messages name the
missing variables only; values (passwords, keys, URLs with credentials) are never echoed, and the
settings ``repr`` redacts every secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from urllib.parse import urlparse

from dotenv import load_dotenv

# Auth issues HS* tokens with a shared secret; asymmetric algorithms would need a key pair.
SUPPORTED_JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}
DEFAULT_SIGNED_URL_TTL_SECONDS = 300
DEFAULT_JPEG_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_GIF_MAX_BYTES = 5 * 1024 * 1024
# Operational settings of the internal jobs (not clinical policy): rows per purge batch, and the
# minimum age of an unreferenced object before reconciliation may delete it (upload in flight).
DEFAULT_PURGE_BATCH_SIZE = 100
DEFAULT_ORPHAN_GRACE_SECONDS = 3600
_SECRET_FIELDS = {"database_url", "redis_url", "jwt_secret_key", "s3_access_key_id", "s3_secret_access_key"}


class ConfigError(RuntimeError):
    """Invalid or missing configuration. The message never contains secret values."""


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _int_from_env(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None


def _float_from_env(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number") from None


def _database_url_from_env() -> str:
    """DATABASE_URL, or the DB_* components used by the other services (all required then)."""
    if _env("DATABASE_URL"):
        return _env("DATABASE_URL")
    if not (_env("DB_HOST") and _env("DB_NAME") and _env("DB_USER")):
        return ""
    return (
        f"postgresql+psycopg://{_env('DB_USER')}:{os.getenv('DB_PASSWORD', '')}@"
        f"{_env('DB_HOST')}:{_env('DB_PORT') or '5432'}/{_env('DB_NAME')}"
    )


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    jwt_secret_key: str
    s3_internal_endpoint: str
    s3_public_endpoint: str
    s3_region: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_bucket_evidence: str
    jwt_algorithm: str = "HS256"
    signed_url_ttl_seconds: int = DEFAULT_SIGNED_URL_TTL_SECONDS
    jpeg_max_bytes: int = DEFAULT_JPEG_MAX_BYTES
    gif_max_bytes: int = DEFAULT_GIF_MAX_BYTES
    purge_batch_size: int = DEFAULT_PURGE_BATCH_SIZE
    orphan_grace_seconds: int = DEFAULT_ORPHAN_GRACE_SECONDS
    host: str = "0.0.0.0"
    port: int = 5005
    log_level: str = "INFO"
    dependency_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        required = {
            "DATABASE_URL (or DB_HOST/DB_NAME/DB_USER)": self.database_url,
            "REDIS_URL": self.redis_url,
            "JWT_SECRET_KEY": self.jwt_secret_key,
            "S3_INTERNAL_ENDPOINT": self.s3_internal_endpoint,
            "S3_PUBLIC_ENDPOINT": self.s3_public_endpoint,
            "S3_REGION": self.s3_region,
            "S3_ACCESS_KEY_ID": self.s3_access_key_id,
            "S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
            "S3_BUCKET_EVIDENCE": self.s3_bucket_evidence,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigError(f"Missing required configuration: {', '.join(missing)}")
        if self.jwt_algorithm not in SUPPORTED_JWT_ALGORITHMS:
            raise ConfigError(f"JWT_ALGORITHM must be one of {sorted(SUPPORTED_JWT_ALGORITHMS)}")
        for name, value in (("S3_INTERNAL_ENDPOINT", self.s3_internal_endpoint),
                            ("S3_PUBLIC_ENDPOINT", self.s3_public_endpoint)):
            if not _is_http_url(value):
                raise ConfigError(f"{name} must be an http(s) URL")
        for name, value in (("EVIDENCE_SIGNED_URL_TTL_SECONDS", self.signed_url_ttl_seconds),
                            ("EVIDENCE_JPEG_MAX_BYTES", self.jpeg_max_bytes),
                            ("EVIDENCE_GIF_MAX_BYTES", self.gif_max_bytes),
                            ("EVIDENCE_PURGE_BATCH_SIZE", self.purge_batch_size)):
            if value <= 0:
                raise ConfigError(f"{name} must be a positive integer")
        if self.orphan_grace_seconds < 0:
            raise ConfigError("EVIDENCE_ORPHAN_GRACE_SECONDS must be zero or positive")
        if self.dependency_timeout_seconds <= 0:
            raise ConfigError("EVIDENCE_DEPENDENCY_TIMEOUT_SECONDS must be positive")

    def __repr__(self) -> str:   # never leak secrets through logs or tracebacks
        parts = [f"{f.name}={'***' if f.name in _SECRET_FIELDS else getattr(self, f.name)!r}"
                 for f in fields(self)]
        return f"Settings({', '.join(parts)})"

    __str__ = __repr__

    @classmethod
    def from_env(cls) -> "Settings":
        # Searches upward from this file: apps/EvidenceService/.env, then the repository root .env.
        # Variables already present in the environment (Docker) always win.
        load_dotenv(override=False)
        return cls(
            database_url=_database_url_from_env(),
            redis_url=_env("REDIS_URL"),
            jwt_secret_key=_env("JWT_SECRET_KEY"),
            jwt_algorithm=_env("JWT_ALGORITHM") or "HS256",
            s3_internal_endpoint=_env("S3_INTERNAL_ENDPOINT"),
            s3_public_endpoint=_env("S3_PUBLIC_ENDPOINT"),
            s3_region=_env("S3_REGION"),
            s3_access_key_id=_env("S3_ACCESS_KEY_ID"),
            s3_secret_access_key=_env("S3_SECRET_ACCESS_KEY"),
            s3_bucket_evidence=_env("S3_BUCKET_EVIDENCE"),
            signed_url_ttl_seconds=_int_from_env("EVIDENCE_SIGNED_URL_TTL_SECONDS", DEFAULT_SIGNED_URL_TTL_SECONDS),
            jpeg_max_bytes=_int_from_env("EVIDENCE_JPEG_MAX_BYTES", DEFAULT_JPEG_MAX_BYTES),
            gif_max_bytes=_int_from_env("EVIDENCE_GIF_MAX_BYTES", DEFAULT_GIF_MAX_BYTES),
            purge_batch_size=_int_from_env("EVIDENCE_PURGE_BATCH_SIZE", DEFAULT_PURGE_BATCH_SIZE),
            orphan_grace_seconds=_int_from_env("EVIDENCE_ORPHAN_GRACE_SECONDS", DEFAULT_ORPHAN_GRACE_SECONDS),
            host=_env("EVIDENCE_HOST") or "0.0.0.0",
            port=_int_from_env("EVIDENCE_PORT", 5005),
            log_level=(_env("EVIDENCE_LOG_LEVEL") or "INFO").upper(),
            dependency_timeout_seconds=_float_from_env("EVIDENCE_DEPENDENCY_TIMEOUT_SECONDS", 3.0),
        )
