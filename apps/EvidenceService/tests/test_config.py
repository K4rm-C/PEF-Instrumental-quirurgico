"""Settings.from_env: required values, validation, defaults and secret redaction."""

from __future__ import annotations

import pytest

import config
from config import ConfigError, Settings

SECRETS = {
    "DATABASE_URL": "postgresql+psycopg://svc:db-pass-XYZ@db:5432/pef",
    "REDIS_URL": "redis://:redis-pass-XYZ@redis:6379/0",
    "JWT_SECRET_KEY": "jwt-secret-XYZ",
    "S3_ACCESS_KEY_ID": "access-key-XYZ",
    "S3_SECRET_ACCESS_KEY": "secret-key-XYZ",
}
VALID_ENV = {
    **SECRETS,
    "JWT_ALGORITHM": "HS256",
    "S3_INTERNAL_ENDPOINT": "http://garage:3900",
    "S3_PUBLIC_ENDPOINT": "http://localhost:3900",
    "S3_REGION": "garage",
    "S3_BUCKET_EVIDENCE": "evidence",
}
ALL_VARS = list(VALID_ENV) + ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT", "EVIDENCE_PORT",
                              "EVIDENCE_SIGNED_URL_TTL_SECONDS", "EVIDENCE_JPEG_MAX_BYTES", "EVIDENCE_GIF_MAX_BYTES",
                              "EVIDENCE_HOST", "EVIDENCE_LOG_LEVEL", "EVIDENCE_DEPENDENCY_TIMEOUT_SECONDS"]


@pytest.fixture
def env(monkeypatch):
    """Clean, deterministic environment (no .env file is loaded)."""
    monkeypatch.setattr(config, "load_dotenv", lambda **kwargs: None)
    for name in ALL_VARS:
        monkeypatch.delenv(name, raising=False)

    def apply(**values):
        for name, value in {**VALID_ENV, **values}.items():
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        return Settings.from_env

    return apply


def _assert_no_secret(text: str) -> None:
    for value in SECRETS.values():
        assert value not in text
    for fragment in ("db-pass-XYZ", "redis-pass-XYZ"):
        assert fragment not in text


def test_valid_configuration_and_defaults(env):
    settings = env()()
    assert settings.port == 5005 and settings.signed_url_ttl_seconds == 300
    assert settings.jpeg_max_bytes == 2 * 1024 * 1024 and settings.gif_max_bytes == 5 * 1024 * 1024
    assert settings.s3_internal_endpoint == "http://garage:3900" and settings.s3_public_endpoint == "http://localhost:3900"


def test_limits_and_ttl_are_configurable(env):
    settings = env(EVIDENCE_SIGNED_URL_TTL_SECONDS="120", EVIDENCE_JPEG_MAX_BYTES="1000",
                   EVIDENCE_GIF_MAX_BYTES="2000", EVIDENCE_PORT="6000")()
    assert (settings.signed_url_ttl_seconds, settings.jpeg_max_bytes, settings.gif_max_bytes, settings.port) == (
        120, 1000, 2000, 6000)


@pytest.mark.parametrize("missing, label", [
    ("DATABASE_URL", "DATABASE_URL"), ("REDIS_URL", "REDIS_URL"), ("JWT_SECRET_KEY", "JWT_SECRET_KEY"),
    ("S3_INTERNAL_ENDPOINT", "S3_INTERNAL_ENDPOINT"), ("S3_PUBLIC_ENDPOINT", "S3_PUBLIC_ENDPOINT"),
    ("S3_REGION", "S3_REGION"), ("S3_ACCESS_KEY_ID", "S3_ACCESS_KEY_ID"),
    ("S3_SECRET_ACCESS_KEY", "S3_SECRET_ACCESS_KEY"), ("S3_BUCKET_EVIDENCE", "S3_BUCKET_EVIDENCE"),
])
def test_missing_required_value(env, missing, label):
    with pytest.raises(ConfigError) as info:
        env(**{missing: None})()
    message = str(info.value)
    assert label in message and "Missing required configuration" in message
    _assert_no_secret(message)


def test_database_from_components(env):
    settings = env(DATABASE_URL=None, DB_HOST="db", DB_NAME="pef", DB_USER="svc", DB_PASSWORD="p")()
    assert settings.database_url == "postgresql+psycopg://svc:p@db:5432/pef"


def test_incomplete_database_components_are_missing(env):
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        env(DATABASE_URL=None, DB_HOST="db")()


@pytest.mark.parametrize("algorithm", ["RS256", "none", "hs256", "ES256"])
def test_invalid_jwt_algorithm(env, algorithm):
    with pytest.raises(ConfigError, match="JWT_ALGORITHM"):
        env(JWT_ALGORITHM=algorithm)()


@pytest.mark.parametrize("name, value", [("S3_INTERNAL_ENDPOINT", "garage:3900"), ("S3_PUBLIC_ENDPOINT", "ftp://x"),
                                         ("S3_INTERNAL_ENDPOINT", "http://")])
def test_endpoints_must_be_http_urls(env, name, value):
    with pytest.raises(ConfigError, match=name):
        env(**{name: value})()


@pytest.mark.parametrize("name, value", [("EVIDENCE_SIGNED_URL_TTL_SECONDS", "0"), ("EVIDENCE_JPEG_MAX_BYTES", "-1"),
                                         ("EVIDENCE_GIF_MAX_BYTES", "abc"), ("EVIDENCE_PORT", "x")])
def test_invalid_numbers(env, name, value):
    with pytest.raises(ConfigError, match=name):
        env(**{name: value})()


def test_repr_never_contains_secrets(env):
    settings = env()()
    text = repr(settings) + str(settings)
    _assert_no_secret(text)
    assert "***" in text and "http://garage:3900" in text


def test_job_settings_defaults_and_overrides(env):
    defaults = env()()
    assert (defaults.purge_batch_size, defaults.orphan_grace_seconds) == (100, 3600)
    custom = env(EVIDENCE_PURGE_BATCH_SIZE="25", EVIDENCE_ORPHAN_GRACE_SECONDS="0")()
    assert (custom.purge_batch_size, custom.orphan_grace_seconds) == (25, 0)


@pytest.mark.parametrize("name, value", [("EVIDENCE_PURGE_BATCH_SIZE", "0"), ("EVIDENCE_PURGE_BATCH_SIZE", "x"),
                                         ("EVIDENCE_ORPHAN_GRACE_SECONDS", "-1"), ("EVIDENCE_ORPHAN_GRACE_SECONDS", "1h")])
def test_invalid_job_settings(env, name, value):
    with pytest.raises(ConfigError, match=name):
        env(**{name: value})()
