"""Integration Service configuration (environment variables only).

Upstream services (owners of the business rules) receive the operations the sync API forwards; FHIR delivery
targets are restricted to INTEGRATION_ALLOWED_HOSTS (no host is allowed by default: nothing is ever sent to a
system that was not explicitly configured).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

SUPPORTED_JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _database_url_from_env() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url
    return (
        "postgresql+psycopg://"
        f"{os.getenv('DB_USER', 'postgres')}:{os.getenv('DB_PASSWORD', '')}@"
        f"{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/"
        f"{os.getenv('DB_NAME', 'postgres')}"
    )


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    host: str = "0.0.0.0"
    port: int = 5009
    log_level: str = "INFO"
    max_content_length: int = 64 * 1024
    dependency_timeout_seconds: float = 3.0
    apply_migrations: bool = True
    # Sync feed
    cursor_secret: str = ""                  # HMAC of the opaque cursors (derived from the JWT secret if empty)
    cursor_max_age_days: int = 30            # older cursors answer 410 cursor_expired (client restarts from scratch)
    retention_days: int = 30                 # superseded changes older than this are compacted (latest per resource kept)
    page_max: int = 500
    # Operations forwarded to the owner services (with the caller's own token)
    upstreams: dict[str, str] = field(default_factory=dict)   # session / catalog / notifications -> base URL
    upstream_timeout_seconds: float = 15.0
    operation_lease_seconds: float = 30.0    # an in-progress operation older than this is reconciled on retry
    # FHIR delivery
    allowed_hosts: tuple[str, ...] = ()      # host[:port] allowed as FHIR delivery targets
    allow_plain_http: bool = False           # only for the isolated test stack
    delivery_timeout_seconds: float = 15.0
    delivery_max_attempts: int = 5
    delivery_backoff_seconds: float = 5.0
    worker_poll_seconds: float = 2.0
    worker_only: bool = False

    def __post_init__(self) -> None:
        if not self.jwt_secret_key and not self.worker_only:
            raise RuntimeError("JWT_SECRET_KEY must be configured for Integration Service")
        if self.jwt_algorithm not in SUPPORTED_JWT_ALGORITHMS:
            raise RuntimeError(f"JWT_ALGORITHM must be one of {sorted(SUPPORTED_JWT_ALGORITHMS)}")

    @property
    def cursor_key(self) -> bytes:
        if self.cursor_secret:
            return self.cursor_secret.encode("utf-8")
        return hmac.new(self.jwt_secret_key.encode("utf-8"), b"pef-sync-cursor-v1", hashlib.sha256).digest()

    @classmethod
    def from_env(cls, *, worker_only: bool = False) -> "Settings":
        load_dotenv(override=False)
        hosts = tuple(h.strip().lower() for h in os.getenv("INTEGRATION_ALLOWED_HOSTS", "").split(",") if h.strip())
        return cls(
            database_url=_database_url_from_env(),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            jwt_secret_key=os.getenv("JWT_SECRET_KEY", ""),
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            host=os.getenv("INTEGRATION_HOST", "0.0.0.0"),
            port=_int("INTEGRATION_PORT", 5009),
            log_level=os.getenv("INTEGRATION_LOG_LEVEL", "INFO").upper(),
            dependency_timeout_seconds=_float("INTEGRATION_DEPENDENCY_TIMEOUT_SECONDS", 3.0),
            apply_migrations=os.getenv("INTEGRATION_APPLY_MIGRATIONS", "true").lower() == "true",
            cursor_secret=os.getenv("SYNC_CURSOR_SECRET", ""),
            cursor_max_age_days=max(1, _int("SYNC_CURSOR_MAX_AGE_DAYS", 30)),
            retention_days=max(1, _int("SYNC_RETENTION_DAYS", 30)),
            page_max=max(10, min(1000, _int("SYNC_PAGE_MAX", 500))),
            upstreams={name: os.getenv(f"INTEGRATION_{name.upper()}_URL", "").strip().rstrip("/")
                       for name in ("session", "catalog", "notifications")},
            upstream_timeout_seconds=_float("INTEGRATION_UPSTREAM_TIMEOUT_SECONDS", 15.0),
            operation_lease_seconds=max(5.0, _float("INTEGRATION_OPERATION_LEASE_SECONDS", 30.0)),
            allowed_hosts=hosts,
            allow_plain_http=os.getenv("INTEGRATION_ALLOW_PLAIN_HTTP", "false").lower() == "true",
            delivery_timeout_seconds=_float("INTEGRATION_DELIVERY_TIMEOUT_SECONDS", 15.0),
            delivery_max_attempts=max(1, _int("INTEGRATION_DELIVERY_MAX_ATTEMPTS", 5)),
            delivery_backoff_seconds=max(0.1, _float("INTEGRATION_DELIVERY_BACKOFF_SECONDS", 5.0)),
            worker_poll_seconds=max(0.2, _float("INTEGRATION_WORKER_POLL_SECONDS", 2.0)),
            worker_only=worker_only,
        )
