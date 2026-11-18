"""MS-12 (ms-integracion-hospitalaria): traceable batch exports of closed sessions. Real PostgreSQL (rolled back):
who may export, institution isolation, exact bytes + SHA-256, items, immutability, audit, no patient data."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest
from lxml import etree
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

BASE = "/api/v1/integration/exports"
XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / "schemas" / "integration.xsd")))
PERIOD = {"period_from": "2026-11-01T00:00:00Z", "period_to": "2026-11-05T00:00:00Z"}


def _session(db_conn, world, key="A", *, status="closed", ended="2026-11-03T10:00:00Z"):
    session_id = uuid.uuid4()
    db_conn.execute(text("""
        INSERT INTO work_session (id, started_at, ended_at, status_id, user_id, operation_id, capture_mode)
        VALUES (:id, '2026-11-03T09:15:00Z', :ended, (SELECT id FROM cat_session_status WHERE code = :st), :u, :o, 'vision')"""),
                    {"id": session_id, "ended": ended, "st": status, "u": world.users[f"op_{key.lower()}"].id,
                     "o": world.operations[key]})
    db_conn.execute(text("""INSERT INTO count_event (event_type_id, session_id, occurred_at, user_id, payload)
        VALUES ((SELECT id FROM cat_event_type WHERE code = 'manual_close'), :s, :at, :u, CAST(:p AS jsonb))"""),
                    {"s": session_id, "at": ended or "2026-11-03T10:00:00Z", "u": world.users[f"op_{key.lower()}"].id,
                     "p": json.dumps({"operator_finished": True, "validation_seal": {"sha256": "e" * 64}})})
    return session_id


def test_it_admin_exports_the_closed_sessions_of_the_period(client, world, as_user, db_conn):
    inside = _session(db_conn, world)
    _session(db_conn, world, status="in_progress", ended=None)                 # not closed
    _session(db_conn, world, ended="2026-11-06T10:00:00Z")                      # outside the period
    _session(db_conn, world, "B")                                               # another institution
    created = client.post(BASE, json=PERIOD, headers=as_user("admin_a"))
    assert created.status_code == 201, created.get_data(as_text=True)
    batch = created.get_json()
    assert batch["sessions_count"] == 1 and batch["session_ids"] == [str(inside)]
    content = client.get(f"{BASE}/{batch['id']}/content", headers=as_user("admin_a"))
    assert content.status_code == 200 and content.headers["X-Content-SHA256"] == batch["content_sha256"]
    assert hashlib.sha256(content.data).hexdigest() == batch["content_sha256"]           # exact stored bytes
    document = json.loads(content.data)
    [session] = document["sessions"]
    assert document["format"] == "pef-session-batch/1" and session["session_id"] == str(inside)
    assert session["encounter_id"] == str(world.operations["A"]) and session["validation_seal_sha256"] == "e" * 64
    raw = content.data.decode()
    assert "Test Patient" not in raw and "Dr. Test" not in raw and "patient" not in json.dumps(session)
    actions = db_conn.execute(text("SELECT action FROM access_audit WHERE resource_id = :b ORDER BY occurred_at"),
                              {"b": batch["id"]}).scalars().all()
    assert actions == ["EXPORT_SESSION_BATCH", "DOWNLOAD_SESSION_BATCH"]
    listed = client.get(f"{BASE}?session_id={inside}", headers=as_user("admin_a")).get_json()
    assert [b["id"] for b in listed] == [batch["id"]]


def test_xml_batches_and_the_contract(client, world, as_user, db_conn):
    _session(db_conn, world)
    created = client.post(BASE, json={**PERIOD, "format": "xml"}, headers=as_user("admin_a", Accept="application/xml"))
    assert created.status_code == 201 and XSD.validate(etree.fromstring(created.data)), XSD.error_log
    batch_id = etree.fromstring(created.data).findtext("id")
    content = client.get(f"{BASE}/{batch_id}/content", headers=as_user("admin_a"))
    assert content.mimetype == "application/xml" and etree.fromstring(content.data).tag == "session_batch"
    listed = client.get(BASE, headers=as_user("admin_a", Accept="application/xml"))
    assert XSD.validate(etree.fromstring(listed.data)), XSD.error_log


def test_a_client_needs_the_export_scope_and_other_roles_are_refused(client, world, as_user, as_client, machine, db_conn):
    _session(db_conn, world)
    machine("exporter", scopes=("export.sessions.batch",))
    machine("reader", scopes=("sync.sessions.read",))
    created = client.post(BASE, json=PERIOD, headers=as_client("exporter"))
    assert created.status_code == 201 and created.get_json()["created_by_client_id"] == str(world.clients["exporter"])
    assert client.post(BASE, json=PERIOD, headers=as_client("reader")).status_code == 403
    for role in ("spd_a", "op_a"):
        assert client.post(BASE, json=PERIOD, headers=as_user(role)).status_code == 403
    assert client.get(f"{BASE}/{created.get_json()['id']}", headers=as_user("admin_b")).status_code == 404


@pytest.mark.parametrize("body, status, error", [
    ({"period_from": "2026-11-05T00:00:00Z", "period_to": "2026-11-01T00:00:00Z"}, 422, "validation_error"),
    ({**PERIOD, "format": "csv"}, 422, "validation_error"),
    ({"period_from": "2026-01-01T00:00:00Z", "period_to": "2099-01-01T00:00:00Z"}, 422, "validation_error"),
    ({"period_from": "yesterday", "period_to": "2026-11-05T00:00:00Z"}, 422, "validation_error"),
    ({**PERIOD, "institution_id": str(uuid.uuid4())}, 400, "invalid_request"),
    ({"period_from": "2026-10-01T00:00:00Z", "period_to": "2026-10-02T00:00:00Z"}, 422, "empty_batch"),
])
def test_invalid_requests(client, world, as_user, db_conn, body, status, error):
    _session(db_conn, world)
    response = client.post(BASE, json=body, headers=as_user("admin_a"))
    assert response.status_code == status and response.get_json()["error"] == error


def test_batches_are_immutable(client, world, as_user, db_conn):
    _session(db_conn, world)
    batch = client.post(BASE, json=PERIOD, headers=as_user("admin_a")).get_json()
    savepoint = db_conn.begin_nested()
    with pytest.raises(DBAPIError, match="immutable"):
        db_conn.execute(text("UPDATE integration_export_batch SET sessions_count = 9 WHERE id = :b"), {"b": batch["id"]})
    savepoint.rollback()
