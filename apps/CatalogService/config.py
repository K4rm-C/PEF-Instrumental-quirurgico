"""Catalog Service configuration, read exclusively from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Auth issues HS* tokens with a shared secret; asymmetric algorithms would need a key pair.
SUPPORTED_JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}


def _int_from_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_from_env(name: str, default: float) -> float:
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
    port: int = 5003
    log_level: str = "INFO"
    max_content_length: int = 64 * 1024
    dependency_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        if not self.jwt_secret_key:
            raise RuntimeError("JWT_SECRET_KEY must be configured for Catalog Service")
        if self.jwt_algorithm not in SUPPORTED_JWT_ALGORITHMS:
            raise RuntimeError(
                f"JWT_ALGORITHM must be one of {sorted(SUPPORTED_JWT_ALGORITHMS)}"
            )

    @classmethod
    def from_env(cls) -> "Settings":
        # Searches upward from this file: apps/CatalogService/.env, then the repo root .env.
        # Variables already present in the environment (Docker) always win.
        load_dotenv(override=False)
        return cls(
            database_url=_database_url_from_env(),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            jwt_secret_key=os.getenv("JWT_SECRET_KEY", ""),
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            host=os.getenv("CATALOG_HOST", "0.0.0.0"),
            port=_int_from_env("CATALOG_PORT", 5003),
            log_level=os.getenv("CATALOG_LOG_LEVEL", "INFO").upper(),
            max_content_length=_int_from_env("CATALOG_MAX_CONTENT_LENGTH", 64 * 1024),
            dependency_timeout_seconds=_float_from_env("CATALOG_DEPENDENCY_TIMEOUT_SECONDS", 3.0),
        )
