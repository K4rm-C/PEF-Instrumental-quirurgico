"""GET /api/v1/evidence/{id} (Phase B): scope, blocked/purged, signed URL, audit, failures, correlation."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from conftest import FakeStorage, TransactionalDatabase, make_settings
from evidence_fixtures import audits, edata  # noqa: F401  (fixture)
from main import create_app

FIELDS = ["id", "session_id", "kind", "content_type", "size_bytes", "sha256", "storage_provider",
          "retention_until", "created_at", "blocked", "purged", "signed_url", "signed_url_expires_at"]


def _url(asset_id):
    return f"/api/v1/evidence/{asset_id}"


def _parse_iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _asset_row(db_conn, asset_id):
    return tuple(db_conn.execute(text("SELECT * FROM media_asset WHERE id = :i"), {"i": asset_id}).one())


# --- Available asset ------------------------------------------------------------------------------


def test_available_asset_gets_signed_url_and_one_audit(client, world, edata, as_user, db_conn, storage):
    asset = edata.assets["a1_jpeg"]
    before = _asset_row(db_conn, asset)
    started = datetime.now(timezone.utc)
    response = client.get(_url(asset), headers=as_user("op_a", **{"X-Correlation-ID": "corr-detail-1"}))
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert list(body) == FIELDS
    assert body["id"] == str(asset) and body["blocked"] is False and body["purged"] is False
    assert body["signed_url"].startswith("http://localhost:3900/evidence/")
    expires = _parse_iso(body["signed_url_expires_at"])
    assert timedelta(seconds=299) <= expires - started <= timedelta(seconds=302)       # TTL 300 by default
    # Only signing: no HeadBucket/HeadObject/GetObject on a detail.
    object_key = db_conn.execute(text("SELECT object_key FROM media_asset WHERE id = :i"), {"i": asset}).scalar_one()
    assert storage.calls == [f"sign:evidence/{object_key}:300"]
    [audit] = audits(db_conn, asset)
    assert audit == {"action": "evidence.download_url", "resource_type": "media_asset", "resource_id": asset,
                     "actor_type": "user", "actor_user_id": world.users["op_a"].id,
                     "institution_id": world.institutions["A"], "outcome": "success", "correlation_id": "corr-detail-1"}
    assert _asset_row(db_conn, asset) == before                    # no domain write


def test_audit_row_stores_no_url_or_location(client, world, edata, as_user, db_conn):
    asset = edata.assets["a1_jpeg"]
    client.get(_url(asset), headers=as_user("op_a"))
    row = db_conn.execute(text("SELECT * FROM access_audit WHERE resource_id = :r"), {"r": asset}).mappings().one()
    stored = " ".join(str(v) for v in row.values())
    for fragment in ("X-Amz", "http", "evidence/", ".jpg", "pytest-access-key", "pytest-s3-secret"):
        assert fragment not in stored


def test_each_emission_is_audited(client, world, edata, as_user, db_conn):
    asset = edata.assets["a1_gif"]
    first = client.get(_url(asset), headers=as_user("spd_a")).get_json()
    second = client.get(_url(asset), headers=as_user("spd_a")).get_json()
    assert first["signed_url"] and second["signed_url"]
    assert len(audits(db_conn, asset)) == 2                        # no URL caching: every bearer URL is audited


def test_ttl_is_configurable(db_conn, redis_client, world, edata, tokens):
    storage = FakeStorage()
    app = create_app(make_settings(signed_url_ttl_seconds=60), database=TransactionalDatabase(db_conn),
                     redis_client=redis_client, storage=storage)
    user = world.users["op_a"]
    started = datetime.now(timezone.utc)
    body = app.test_client().get(_url(edata.assets["a1_jpeg"]), headers={
        "Authorization": f"Bearer {tokens.issue(user.id, user.institution_id)}"}).get_json()
    assert storage.calls[0].endswith(":60")
    assert timedelta(seconds=59) <= _parse_iso(body["signed_url_expires_at"]) - started <= timedelta(seconds=62)


# --- Blocked / purged / retention -------------------------------------------------------------------


@pytest.mark.parametrize("key, blocked, purged", [("a1_blocked", True, False), ("a1_purged", False, True)])
def test_blocked_or_purged_is_metadata_only(client, world, edata, as_user, db_conn, storage, key, blocked, purged):
    response = client.get(_url(edata.assets[key]), headers=as_user("spd_a"))
    assert response.status_code == 200
    body = response.get_json()
    assert list(body) == FIELDS and (body["blocked"], body["purged"]) == (blocked, purged)
    assert body["signed_url"] is None and body["signed_url_expires_at"] is None
    assert storage.calls == [] and audits(db_conn) == []          # no signing, no audit, no storage call


def test_expired_retention_alone_does_not_block_serving(client, world, edata, as_user, db_conn):
    """retention_until in the past is the purge job's business (DS05 rule 2); serving follows blocked/purged only."""
    asset = edata.assets["a1_jpeg"]
    db_conn.execute(text("UPDATE media_asset SET retention_until = now() - interval '1 day' WHERE id = :i"), {"i": asset})
    body = client.get(_url(asset), headers=as_user("op_a")).get_json()
    assert body["signed_url"] is not None and len(audits(db_conn, asset)) == 1


# --- Access ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("user_key, asset_key, status", [
    ("op_a", "a1_jpeg", 200),
    ("op_a", "a2", 404),          # same tenant, session assigned to another operator
    ("op_a", "b1", 404),          # other tenant
    ("op_a2", "a2", 200),
    ("spd_a", "a2", 200),
    ("spd_a", "b1", 404),
    ("spd_b", "b1", 200),
    ("multi_a", "a2", 200),       # supervisor scope
    ("op_a", "orphan", 404),      # session_id NULL: outside the clinical API
    ("spd_a", "orphan", 404),
    ("op_a", "noop", 404),        # session without operation
    ("spd_a", "noop", 404),
    ("admin_a", "a1_jpeg", 403),
    ("viewer_a", "a1_jpeg", 403),
])
def test_access_rules(client, world, edata, as_user, db_conn, user_key, asset_key, status):
    response = client.get(_url(edata.assets[asset_key]), headers=as_user(user_key))
    assert response.status_code == status
    if status != 200:
        assert response.get_json()["error"] == ("forbidden" if status == 403 else "not_found")
        assert audits(db_conn) == []


def test_404s_are_indistinguishable(client, world, edata, as_user):
    bodies = [client.get(_url(a), headers=as_user("op_a")).get_json() for a in
              (edata.assets["a2"], edata.assets["b1"], edata.assets["orphan"], edata.assets["noop"], uuid.uuid4(),
               "not-a-uuid")]
    assert {(b["error"], b["message"]) for b in bodies} == {("not_found", "Evidence not found")}


def test_403_is_decided_before_any_lookup(client, world, edata, as_user):
    for asset in (edata.assets["a1_jpeg"], edata.assets["b1"], uuid.uuid4(), "not-a-uuid"):
        assert client.get(_url(asset), headers=as_user("admin_a")).status_code == 403


def test_orphan_asset_never_resolved_from_object_key(client, world, edata, as_user, db_conn):
    """An asset without session whose key LOOKS like tenant A / session a1 is still 404."""
    sid = edata.sessions["a1"].id
    db_conn.execute(text("UPDATE media_asset SET object_key = :k WHERE id = :i"),
                    {"k": f"{world.institutions['A']}/{sid}/2026/fake.jpg", "i": edata.assets["orphan"]})
    assert client.get(_url(edata.assets["orphan"]), headers=as_user("spd_a")).status_code == 404


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not.a.jwt"}, {"Authorization": "Basic x"}])
def test_authentication_required(client, world, edata, headers):
    assert client.get(_url(edata.assets["a1_jpeg"]), headers=headers).status_code == 401


def test_revoked_token(client, world, edata, tokens):
    user = world.users["op_a"]
    token = tokens.issue(user.id, user.institution_id, revoked=True)
    assert client.get(_url(edata.assets["a1_jpeg"]), headers={"Authorization": f"Bearer {token}"}).status_code == 401


# --- XML ---------------------------------------------------------------------------------------------


def test_xml_detail(client, world, edata, as_user):
    root = ET.fromstring(client.get(_url(edata.assets["a1_jpeg"]), headers=as_user("op_a", Accept="application/xml")).data)
    assert root.tag == "evidence" and [c.tag for c in root] == FIELDS
    assert root.findtext("signed_url").startswith("http://localhost:3900/") and root.findtext("blocked") == "false"
    nil = "{http://www.w3.org/2001/XMLSchema-instance}nil"
    blocked = ET.fromstring(client.get(_url(edata.assets["a1_blocked"]),
                                       headers=as_user("op_a", Accept="application/xml")).data)
    assert blocked.find("signed_url").get(nil) == "true" and blocked.findtext("blocked") == "true"


def test_xml_errors(client, world, edata, as_user):
    response = client.get(_url(edata.assets["b1"]), headers=as_user("op_a", Accept="application/xml"))
    root = ET.fromstring(response.data)
    assert response.status_code == 404 and root.tag == "error_response"
    assert root.findtext("correlation_id") == response.headers["X-Correlation-ID"]


# --- Failures ------------------------------------------------------------------------------------------


def _app_with(db_conn, redis_client, storage):
    return create_app(make_settings(), database=TransactionalDatabase(db_conn), redis_client=redis_client, storage=storage)


def _bearer(tokens, world, key="op_a"):
    user = world.users[key]
    return {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id)}"}


def test_signing_failure_is_503_without_audit(db_conn, redis_client, world, edata, tokens):
    app = _app_with(db_conn, redis_client, FakeStorage(sign_error=True))
    response = app.test_client().get(_url(edata.assets["a1_jpeg"]), headers=_bearer(tokens, world))
    assert response.status_code == 503
    assert response.get_json()["error"] == "dependency_unavailable"
    assert response.get_json()["details"] == {"dependency": "storage"}
    assert "X-Amz" not in response.get_data(as_text=True) and audits(db_conn) == []


@pytest.mark.parametrize("failure", ["constraint", "unexpected"])
def test_audit_failure_never_delivers_the_url(client, app, world, edata, as_user, db_conn, monkeypatch, failure):
    from adapters import audit_adapter

    if failure == "constraint":
        monkeypatch.setattr(audit_adapter, "ACTOR_TYPE", "robot")      # violates the access_audit CHECK
    else:
        def boom(*args, **kwargs):
            raise RuntimeError("audit insert exploded with http://localhost:3900/evidence/secret-key")
        monkeypatch.setattr(audit_adapter, "record", boom)
    asset = edata.assets["a1_jpeg"]
    before = _asset_row(db_conn, asset)
    response = client.get(_url(asset), headers=as_user("op_a"))
    assert response.status_code == 500 and response.get_json()["error"] == "internal_error"
    text_body = response.get_data(as_text=True)
    assert "X-Amz" not in text_body and "signed_url" not in text_body and "localhost:3900" not in text_body
    assert audits(db_conn) == [] and _asset_row(db_conn, asset) == before


def test_database_lookup_failure_is_503(client, world, edata, as_user, monkeypatch, db_conn):
    from repositories import evidence_repository

    def broken(*args, **kwargs):
        raise OperationalError("SELECT secret FROM media_asset", {}, Exception("connection refused"))
    monkeypatch.setattr(evidence_repository, "get_clinical_asset", broken)
    response = client.get(_url(edata.assets["a1_jpeg"]), headers=as_user("op_a"))
    assert response.status_code == 503 and response.get_json()["details"] == {"dependency": "database"}
    assert "SELECT" not in response.get_data(as_text=True) and audits(db_conn) == []


def test_storage_outage_does_not_affect_signing(db_conn, redis_client, world, edata, tokens):
    """Presigning is local: with storage down (/health/storage 503) a detail can still emit a URL."""
    storage = FakeStorage(failure="unreachable")
    client = _app_with(db_conn, redis_client, storage).test_client()
    assert client.get("/health/storage").status_code == 503
    body = client.get(_url(edata.assets["a1_jpeg"]), headers=_bearer(tokens, world)).get_json()
    assert body["signed_url"] is not None


# --- Correlation ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("incoming, preserved", [("c" * 64, True), ("c" * 65, False)])
def test_audit_correlation_equals_response_header(client, world, edata, as_user, db_conn, incoming, preserved):
    asset = edata.assets["a1_jpeg"]
    response = client.get(_url(asset), headers=as_user("op_a", **{"X-Correlation-ID": incoming}))
    answered = response.headers["X-Correlation-ID"]
    assert (answered == incoming) is preserved
    assert not preserved or len(answered) == 64
    if not preserved:
        assert str(uuid.UUID(answered)) == answered
    assert [a["correlation_id"] for a in audits(db_conn, asset)] == [answered]


def test_newline_correlation_is_replaced_on_business_route(client, world, edata, as_user, db_conn):
    asset = edata.assets["a1_jpeg"]
    response = client.get(_url(asset), headers=as_user("op_a"), environ_overrides={"HTTP_X_CORRELATION_ID": "abc\n"})
    answered = response.headers["X-Correlation-ID"]
    assert str(uuid.UUID(answered)) == answered
    assert [a["correlation_id"] for a in audits(db_conn, asset)] == [answered]


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_error_correlation_on_business_routes(client, world, edata, as_user, accept):
    response = client.get(_url(edata.assets["b1"]), headers=as_user("op_a", Accept=accept,
                                                                     **{"X-Correlation-ID": "e" * 65}))
    answered = response.headers["X-Correlation-ID"]
    body = response.get_json()["correlation_id"] if accept.endswith("json") else \
        ET.fromstring(response.data).findtext("correlation_id")
    assert response.status_code == 404 and body == answered and str(uuid.UUID(answered)) == answered
