"""Scope guards (Phase A/B): only health + the two read endpoints, no media writes, independent code, schema."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from conftest import FakeStorage, TransactionalDatabase, make_settings
from main import create_app

SERVICE_DIR = Path(__file__).resolve().parents[1]


def test_only_health_read_and_upload_routes_exist(db_conn, redis_client):
    app = create_app(make_settings(), database=TransactionalDatabase(db_conn), redis_client=redis_client,
                     storage=FakeStorage())
    routes = {(rule.rule, method) for rule in app.url_map.iter_rules() if rule.endpoint != "static"
              for method in rule.methods - {"HEAD", "OPTIONS"}}
    health = {(f"/health/{name}", "GET") for name in ("live", "database", "redis", "storage", "ready")}
    api = {("/api/v1/evidence/<asset_id>", "GET"), ("/api/v1/sessions/<session_id>/evidence", "GET"),
           ("/api/v1/evidence", "POST")}
    assert routes == health | api


@pytest.mark.parametrize("method, path", [("put", "/api/v1/evidence"), ("put", "/api/v1/evidence/x"),
                                          ("delete", "/api/v1/evidence/x"), ("post", "/api/v1/sessions/x/evidence")])
def test_update_delete_endpoints_do_not_exist(db_conn, redis_client, method, path):
    app = create_app(make_settings(), database=TransactionalDatabase(db_conn), redis_client=redis_client,
                     storage=FakeStorage())
    assert getattr(app.test_client(), method)(path).status_code in (404, 405)


def test_health_never_writes_media_asset(client, db_conn):
    before = db_conn.execute(text("SELECT count(*) FROM media_asset")).scalar_one()
    for path in ("/health/live", "/health/database", "/health/redis", "/health/storage", "/health/ready"):
        client.get(path)
    assert db_conn.execute(text("SELECT count(*) FROM media_asset")).scalar_one() == before


def _imported_roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_code_is_independent_of_other_services():
    """Only the stdlib, the declared dependencies and this service's own modules are imported."""
    local = {p.stem for p in SERVICE_DIR.glob("*.py")} | {p.name for p in SERVICE_DIR.iterdir() if p.is_dir()}
    third_party = {"flask", "werkzeug", "jwt", "sqlalchemy", "redis", "dotenv", "boto3", "botocore"}
    sources = [p for p in SERVICE_DIR.rglob("*.py") if ".venv" not in p.parts and "tests" not in p.parts]
    for path in sources:
        unexpected = _imported_roots(path) - local - third_party - set(sys.stdlib_module_names) - {"__future__"}
        assert not unexpected, f"{path.name} imports {unexpected}"
        code = path.read_text(encoding="utf-8")
        for call in ("create_all(", "create_bucket", "upload_file", "get_object(", "delete_bucket",
                     "DELETE FROM media_asset", "media_asset.delete("):
            assert call not in code, f"{call} in {path.name}"
        if path.name != "s3_adapter.py":   # raw S3 object calls live only in the storage adapter
            for call in ("put_object(", "head_object(", "self._client.", "list_objects"):
                assert call not in code, f"{call} in {path.name}"
        if path.name != "s3_adapter.py":   # signing lives only in the storage adapter
            assert "generate_presigned" not in code or path.name == "evidence_service.py", path.name
    requirements = (SERVICE_DIR / "requirements.txt").read_text(encoding="utf-8").lower()
    for package in ("pymongo", "minio", "requests", "opencv", "ultralytics", "flask-babel"):
        assert package not in requirements


def test_migration_004_is_present_in_the_database(db_conn):
    """Phase B prerequisite (PEF-DS05 v0.2.0): media_asset.session_id with FK RESTRICT and its index."""
    column = db_conn.execute(text("SELECT is_nullable FROM information_schema.columns "
                                  "WHERE table_name = 'media_asset' AND column_name = 'session_id'")).scalar()
    fk = db_conn.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                              "WHERE conname = 'fk_media_asset_session'")).scalar()
    index = db_conn.execute(text("SELECT indexdef FROM pg_indexes "
                                 "WHERE indexname = 'idx_media_asset_session_created'")).scalar()
    assert column == "YES"
    assert fk == "FOREIGN KEY (session_id) REFERENCES work_session(id) ON DELETE RESTRICT"
    assert "(session_id, created_at, id)" in index
