"""POST /api/v1/sessions (Phase C)."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import text

from write_fixtures import committed_env, create_body, wdata  # noqa: F401  (fixtures)

URL = "/api/v1/sessions"
DETAIL_FIELDS = ["id", "operation_id", "user_id", "station_id", "kit_id", "status_code", "capture_mode",
                 "current_phase_code", "scheduled_at", "started_at", "ended_at", "closed_by_user_id",
                 "open_discrepancies", "privacy_agreement"]


def _count(db_conn, sql, **params):
    return db_conn.execute(text(sql), params).scalar_one()


def _sessions_of(db_conn, operation_id):
    return _count(db_conn, "SELECT count(*) FROM work_session WHERE operation_id = :o", o=operation_id)


def _error(response, status, error):
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    assert body["error"] == error and body["correlation_id"] == response.headers["X-Correlation-ID"]
    return body


# --- Happy path ----------------------------------------------------------------------------


def test_create_session_json(client, world, wdata, as_user, db_conn):
    headers = as_user("spd_a", **{"X-Correlation-ID": "corr-create-1"})
    response = client.post(URL, json=create_body(world, wdata), headers=headers)
    assert response.status_code == 201, response.get_data(as_text=True)
    body = response.get_json()
    assert list(body) == DETAIL_FIELDS
    assert body["operation_id"] == str(wdata.op_a) and body["user_id"] == str(world.users["op_a"].id)
    assert body["status_code"] == "scheduled" and body["capture_mode"] is None and body["started_at"] is None
    assert body["current_phase_code"] == "start" and body["scheduled_at"] == "2026-11-01T09:00:00Z"
    assert body["privacy_agreement"] is False and body["open_discrepancies"] == 0
    session_id = body["id"]

    lines = client.get(f"{URL}/{session_id}/expected-inventory", headers=as_user("spd_a")).get_json()
    assert {(l["family_id"], l["expected_quantity"], l["source"]) for l in lines} == {
        (str(world.families["a"]), 2, "kit_snapshot"),          # in kit, same quantity
        (str(world.families["c"]), 1, "schedule_additional"),   # not in kit
    }
    audit = db_conn.execute(text(
        "SELECT action, actor_type, actor_user_id, resource_type, institution_id, outcome, correlation_id, ip "
        "FROM access_audit WHERE resource_id = :s AND outcome = 'success'"), {"s": session_id}).mappings().all()
    assert [dict(a) for a in audit] == [{
        "action": "session.scheduled", "actor_type": "user", "actor_user_id": world.users["spd_a"].id,
        "resource_type": "work_session", "institution_id": world.institutions["A"], "outcome": "success",
        "correlation_id": "corr-create-1", "ip": None,
    }]
    # Nothing outside the Session domain is created or changed.
    assert _count(db_conn, "SELECT count(*) FROM instrument_reservation r JOIN operation o ON o.id = r.operation_id "
                           "WHERE o.id = :o", o=wdata.op_a) == 0
    assert _count(db_conn, "SELECT s.code FROM operation o JOIN cat_operation_status s ON s.id = o.status_id "
                           "WHERE o.id = :o", o=wdata.op_a) == "scheduled"
    assert _count(db_conn, "SELECT count(*) FROM count_event WHERE session_id = :s", s=session_id) == 0


def test_family_in_kit_with_other_quantity_is_schedule_additional(client, world, wdata, as_user):
    body = create_body(world, wdata, expected_lines=[
        {"family_id": str(world.families["a"]), "expected_quantity": 3},   # kit says 2
        {"family_id": str(world.families["b"]), "expected_quantity": 1},   # kit says 1
    ])
    session_id = client.post(URL, json=body, headers=as_user("spd_a")).get_json()["id"]
    lines = client.get(f"{URL}/{session_id}/expected-inventory", headers=as_user("spd_a")).get_json()
    assert {(l["family_id"], l["source"]) for l in lines} == {
        (str(world.families["a"]), "schedule_additional"), (str(world.families["b"]), "kit_snapshot")}


def test_operation_without_room_accepts_tenant_station(client, world, wdata, as_user):
    body = create_body(world, wdata, operation_id=str(wdata.op_a_noroom), station_id=str(wdata.station_a2))
    assert client.post(URL, json=body, headers=as_user("spd_a")).status_code == 201


def test_create_session_xml(client, world, wdata, as_user):
    xml = (
        "<session_request>"
        f"<operation_id>{wdata.op_a}</operation_id><user_id>{world.users['op_a'].id}</user_id>"
        f"<station_id>{world.stations['A']}</station_id><kit_id>{world.kits['A']}</kit_id>"
        "<phase_code>start</phase_code>"
        "<expected_lines>"
        f"<line><family_id>{world.families['a']}</family_id><expected_quantity>2</expected_quantity></line>"
        f"<line><family_id>{world.families['c']}</family_id><expected_quantity>1</expected_quantity></line>"
        "</expected_lines></session_request>"
    )
    response = client.post(URL, data=xml, headers=as_user("spd_a", **{
        "Content-Type": "application/xml", "Accept": "application/xml"}))
    assert response.status_code == 201, response.get_data(as_text=True)
    root = ET.fromstring(response.data)
    assert root.tag == "session" and [c.tag for c in root] == DETAIL_FIELDS
    assert root.findtext("status_code") == "scheduled"
    lines = client.get(f"{URL}/{root.findtext('id')}/expected-inventory", headers=as_user("spd_a")).get_json()
    assert len(lines) == 2


def test_multi_role_supervisor_can_create(client, world, wdata, as_user):
    assert client.post(URL, json=create_body(world, wdata), headers=as_user("multi_a")).status_code == 201


# --- Roles ---------------------------------------------------------------------------------


@pytest.mark.parametrize("user_key", ["op_a", "admin_a", "op_admin_a"])
def test_create_forbidden_without_supervisor_role(client, world, wdata, as_user, db_conn, user_key):
    _error(client.post(URL, json=create_body(world, wdata), headers=as_user(user_key)), 403, "forbidden")
    assert _sessions_of(db_conn, wdata.op_a) == 0


def test_create_requires_token(client, world, wdata):
    _error(client.post(URL, json=create_body(world, wdata)), 401, "unauthorized")


# --- References / tenant -------------------------------------------------------------------


@pytest.mark.parametrize("field, value", [
    ("operation_id", lambda w, d: d.op_b),                       # other tenant
    ("operation_id", lambda w, d: uuid.uuid4()),                 # unknown
    ("user_id", lambda w, d: w.users["op_b"].id),                 # operator of B
    ("user_id", lambda w, d: w.users["spd_a"].id),                # no station_operator role
    ("user_id", lambda w, d: w.users["inactive_a"].id),           # inactive
    ("user_id", lambda w, d: uuid.uuid4()),                       # unknown
    ("station_id", lambda w, d: w.stations["B"]),                 # other tenant
    ("station_id", lambda w, d: d.station_a2),                    # other room than the operation's
    ("station_id", lambda w, d: d.station_inactive),              # inactive
    ("station_id", lambda w, d: uuid.uuid4()),                    # unknown
    ("kit_id", lambda w, d: w.kits["B"]),                         # other tenant
    ("kit_id", lambda w, d: d.kit_inactive),                      # inactive
    ("kit_id", lambda w, d: uuid.uuid4()),                        # unknown
])
def test_invalid_references_are_422(client, world, wdata, as_user, db_conn, field, value):
    body = create_body(world, wdata, **{field: str(value(world, wdata))})
    result = _error(client.post(URL, json=body, headers=as_user("spd_a")), 422, "invalid_reference")
    assert result["details"] == {"field": field}
    assert str(world.institutions["B"]) not in str(result)
    assert _sessions_of(db_conn, wdata.op_a) == 0


def test_operator_whose_role_is_from_other_tenant_is_rejected(client, world, wdata, as_user, db_conn):
    role_b = db_conn.execute(text("SELECT id FROM role WHERE institution_id = :i AND code = 'station_operator'"),
                             {"i": world.institutions["B"]}).scalar_one()
    db_conn.execute(text("DELETE FROM user_role WHERE user_id = :u"), {"u": world.users["op_a"].id})
    db_conn.execute(text("INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)"),
                    {"u": world.users["op_a"].id, "r": role_b})
    _error(client.post(URL, json=create_body(world, wdata), headers=as_user("spd_a")), 422, "invalid_reference")


@pytest.mark.parametrize("phase", ["no_such_phase", "inactive"])
def test_invalid_phase_is_422(client, world, wdata, as_user, phase):
    code = wdata.phase_inactive if phase == "inactive" else phase
    body = create_body(world, wdata, phase_code=code)
    assert _error(client.post(URL, json=body, headers=as_user("spd_a")), 422, "invalid_reference")["details"] == {
        "field": "phase_code"}


@pytest.mark.parametrize("family", ["unknown", "inactive"])
def test_invalid_family_is_422(client, world, wdata, as_user, family):
    family_id = str(uuid.uuid4()) if family == "unknown" else str(wdata.family_inactive)
    body = create_body(world, wdata, expected_lines=[{"family_id": family_id, "expected_quantity": 1}])
    result = _error(client.post(URL, json=body, headers=as_user("spd_a")), 422, "invalid_reference")
    assert result["details"]["family_id"] == family_id


def test_operation_not_scheduled_is_409(client, world, wdata, as_user, db_conn):
    body = create_body(world, wdata, operation_id=str(wdata.op_a_closed))
    _error(client.post(URL, json=body, headers=as_user("spd_a")), 409, "conflict")
    assert _sessions_of(db_conn, wdata.op_a_closed) == 0


# --- Expected lines ------------------------------------------------------------------------


@pytest.mark.parametrize("lines, status, error", [
    ([], 400, "invalid_request"),
    (None, 400, "invalid_request"),
    ("abc", 400, "invalid_request"),
    ([{"family_id": "F_A", "expected_quantity": 1}, {"family_id": "F_A", "expected_quantity": 2}], 400, "invalid_request"),
    ([{"family_id": "F_A", "expected_quantity": 0}], 422, "validation_error"),
    ([{"family_id": "F_A", "expected_quantity": -1}], 422, "validation_error"),
    ([{"family_id": "F_A", "expected_quantity": 32768}], 422, "validation_error"),
    ([{"family_id": "F_A", "expected_quantity": True}], 400, "invalid_request"),
    ([{"family_id": "F_A", "expected_quantity": "2"}], 400, "invalid_request"),
    ([{"family_id": "F_A", "expected_quantity": 1.5}], 400, "invalid_request"),
    ([{"family_id": "F_A", "expected_quantity": 1, "source": "manual"}], 400, "invalid_request"),
    ([{"family_id": "not-a-uuid", "expected_quantity": 1}], 400, "invalid_request"),
])
def test_invalid_expected_lines(client, world, wdata, as_user, db_conn, lines, status, error):
    if isinstance(lines, list):
        lines = [{**l, "family_id": str(world.families["a"]) if l["family_id"] == "F_A" else l["family_id"]}
                 for l in lines]
    body = create_body(world, wdata, expected_lines=lines)
    if lines is None:
        body.pop("expected_lines")
    _error(client.post(URL, json=body, headers=as_user("spd_a")), status, error)
    assert _sessions_of(db_conn, wdata.op_a) == 0


def test_stock_insufficient_is_422(client, world, wdata, as_user, db_conn):
    body = create_body(world, wdata, expected_lines=[{"family_id": str(world.families["a"]), "expected_quantity": 4}])
    result = _error(client.post(URL, json=body, headers=as_user("spd_a")), 422, "validation_error")
    assert result["details"] == {"family_id": str(world.families["a"]), "reason": "stock_block"}
    assert _sessions_of(db_conn, wdata.op_a) == 0


@pytest.mark.parametrize("suffix, qty, ok", [
    ("a", 3, True),    # exactly A's available pieces (B has 5 more that must not count)
    ("d", 1, False),   # A only has an inactive d; B has 5 available
    ("c", 2, False),   # A has one available + one in maintenance
])
def test_stock_is_tenant_safe_and_counts_only_available(client, world, wdata, as_user, suffix, qty, ok):
    body = create_body(world, wdata, expected_lines=[{"family_id": str(world.families[suffix]), "expected_quantity": qty}])
    status = client.post(URL, json=body, headers=as_user("spd_a")).status_code
    assert status == (201 if ok else 422)


# --- Body shape ----------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [{"institution_id": "x"}, {"scheduled_at": "2026-01-01"}, {"patient_id": "x"},
                                   {"room_id": "x"}, {"procedure_type_id": "x"}, {"capture_mode": "vision"}])
def test_unknown_fields_are_400(client, world, wdata, as_user, extra):
    body = {**create_body(world, wdata), **extra}
    result = _error(client.post(URL, json=body, headers=as_user("spd_a")), 400, "invalid_request")
    assert result["details"] == {"unknown_fields": sorted(extra)}


@pytest.mark.parametrize("field", ["operation_id", "user_id", "station_id", "kit_id", "phase_code"])
def test_missing_field_is_400(client, world, wdata, as_user, field):
    body = create_body(world, wdata)
    body.pop(field)
    assert _error(client.post(URL, json=body, headers=as_user("spd_a")), 400, "invalid_request")["details"] == {
        "missing_fields": [field]}


@pytest.mark.parametrize("data, content_type", [
    ("not json", "application/json"), ("[1]", "application/json"), ("x=1", "text/plain"), ("", "application/json"),
    ("<other/>", "application/xml"), ('<!DOCTYPE x [<!ENTITY e "x">]><session_request/>', "application/xml"),
    ("<session_request><kit_id>1</kit_id><kit_id>2</kit_id></session_request>", "application/xml"),
])
def test_malformed_bodies_are_400(client, world, wdata, as_user, data, content_type):
    response = client.post(URL, data=data, headers=as_user("spd_a", **{"Content-Type": content_type}))
    _error(response, 400, "invalid_request")


def test_body_too_large_is_413(client, world, wdata, as_user):
    response = client.post(URL, data=b'{"x": "' + b"a" * 70_000 + b'"}',
                           headers=as_user("spd_a", **{"Content-Type": "application/json"}))
    _error(response, 413, "payload_too_large")


def test_xml_error_response(client, world, wdata, as_user):
    response = client.post(URL, json={**create_body(world, wdata), "institution_id": "x"},
                           headers=as_user("spd_a", Accept="application/xml"))
    root = ET.fromstring(response.data)
    assert response.status_code == 400 and root.tag == "error_response" and root.findtext("error") == "invalid_request"


# --- Atomicity -----------------------------------------------------------------------------


def test_audit_failure_rolls_back_everything(client, world, wdata, as_user, db_conn, monkeypatch):
    from adapters import audit_adapter

    monkeypatch.setattr(audit_adapter, "ACTOR_TYPE", "robot")   # violates chk_access_audit_actor_type
    response = client.post(URL, json=create_body(world, wdata), headers=as_user("spd_a"))
    _error(response, 500, "internal_error")
    assert "chk_access_audit" not in response.get_data(as_text=True)
    assert _sessions_of(db_conn, wdata.op_a) == 0
    assert _count(db_conn, "SELECT count(*) FROM expected_inventory e JOIN work_session w ON w.id = e.session_id "
                           "WHERE w.operation_id = :o", o=wdata.op_a) == 0


def test_database_outage_during_write_is_503_and_rolls_back(client, world, wdata, as_user, db_conn, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from adapters import audit_adapter

    def broken(*args, **kwargs):
        raise OperationalError("INSERT ...", {}, Exception("server closed the connection"))
    monkeypatch.setattr(audit_adapter, "record", broken)
    response = client.post(URL, json=create_body(world, wdata), headers=as_user("spd_a"))
    body = _error(response, 503, "dependency_unavailable")
    assert body["details"] == {"dependency": "database"} and "INSERT" not in response.get_data(as_text=True)
    assert _sessions_of(db_conn, wdata.op_a) == 0


CORR_64 = "corr-" + "6" * 59        # exactly 64 characters (DS09 v0.1.5 maximum)


def _stored_correlation(db_conn, session_id):
    return _count(db_conn, "SELECT correlation_id FROM access_audit WHERE resource_id = :s", s=session_id)


def test_64_char_correlation_id_is_stored_exactly(client, world, wdata, as_user, db_conn):
    assert len(CORR_64) == 64
    response = client.post(URL, json=create_body(world, wdata), headers=as_user("spd_a", **{"X-Correlation-ID": CORR_64}))
    assert response.status_code == 201 and response.headers["X-Correlation-ID"] == CORR_64
    assert _stored_correlation(db_conn, response.get_json()["id"]) == CORR_64


@pytest.mark.parametrize("fmt", ["json", "xml"])
def test_65_char_correlation_id_is_replaced_and_audited_identically(client, world, wdata, as_user, db_conn, fmt):
    """Never truncated: a 65-char header is replaced by a UUID, and audit stores that same UUID."""
    corr = CORR_64 + "x"
    headers = as_user("spd_a", **{"X-Correlation-ID": corr})
    if fmt == "json":
        response = client.post(URL, json=create_body(world, wdata), headers=headers)
        session_id = response.get_json()["id"]
    else:
        body = create_body(world, wdata)
        lines = "".join(f"<line><family_id>{l['family_id']}</family_id><expected_quantity>{l['expected_quantity']}"
                        "</expected_quantity></line>" for l in body["expected_lines"])
        fields = "".join(f"<{k}>{v}</{k}>" for k, v in body.items() if k != "expected_lines")
        response = client.post(URL, data=f"<session_request>{fields}<expected_lines>{lines}</expected_lines></session_request>",
                               headers={**headers, "Content-Type": "application/xml", "Accept": "application/xml"})
        session_id = ET.fromstring(response.data).findtext("id")
    assert response.status_code == 201, response.get_data(as_text=True)
    generated = response.headers["X-Correlation-ID"]
    assert str(uuid.UUID(generated)) == generated
    assert _stored_correlation(db_conn, session_id) == generated
    assert _count(db_conn, "SELECT count(*) FROM access_audit WHERE correlation_id = :c", c=corr[:64]) == 0


def test_double_submit_creates_two_sessions(client, world, wdata, as_user, db_conn):
    """DS04/DS09 define no one-session-per-operation rule nor create idempotency (documented gap)."""
    for _ in range(2):
        assert client.post(URL, json=create_body(world, wdata), headers=as_user("spd_a")).status_code == 201
    assert _sessions_of(db_conn, wdata.op_a) == 2
