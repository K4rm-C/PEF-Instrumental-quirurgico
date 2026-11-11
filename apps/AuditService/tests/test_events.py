"""GET /api/v1/audit/events, /events/{id}, /events/summary — scope, filters, pagination, formats."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from lxml import etree
from sqlalchemy import text

from conftest import NOW

BASE = "/api/v1/audit"
XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / "schemas" / "audit.xsd")))
XML = {"Accept": "application/xml"}


def ids(response):
    return [item["id"] for item in response.get_json()["items"]]


def scoped(world, response):
    """Only the rows this test created (the database may hold other real rows of other institutions)."""
    mine = {str(v) for v in world.events.values()}
    return [i for i in ids(response) if i in mine]


# --- Scope -------------------------------------------------------------------------------------------


def test_admin_sees_every_event_of_the_institution_newest_first(client, world, as_user):
    response = client.get(f"{BASE}/events?per_page=100", headers=as_user("admin_a"))
    assert response.status_code == 200
    expected = ["user_admin", "denied", "manual", "blocked", "initial", "start", "open", "scheduled", "family", "login"]
    got = scoped(world, response)
    # session_open and session.start share a minute: both orders are newest-first.
    assert set(got) == {str(world.events[k]) for k in expected}
    assert str(world.events["foreign"]) not in ids(response)
    times = [item["occurred_at"] for item in response.get_json()["items"]]
    assert times == sorted(times, reverse=True)


def test_supervisor_sees_the_operational_trail_only(client, world, as_user):
    response = client.get(f"{BASE}/events?per_page=100", headers=as_user("spd_a"))
    got = set(scoped(world, response))
    assert got == {str(world.events[k]) for k in ("scheduled", "start", "open", "initial", "blocked", "manual", "denied")}
    for item in response.get_json()["items"]:
        assert item["actor"] is None or item["actor"]["email"] is None   # no e-mail for the Supervisor


@pytest.mark.parametrize("user", ["op_a", "norole_a"])
def test_operator_and_other_roles_are_403_and_audited(client, world, as_user, db_conn, user):
    response = client.get(f"{BASE}/events", headers=as_user(user))
    assert response.status_code == 403 and response.get_json()["error"] == "forbidden"
    rows = db_conn.execute(text("SELECT action, outcome, resource_type FROM access_audit WHERE actor_user_id = :u "
                                "AND resource_type = 'audit_event'"), {"u": world.users[user].id}).all()
    assert [tuple(r) for r in rows] == [("DENIED_GET_AUDIT_EVENT", "denied", "audit_event")]


def test_another_institution_is_out_of_scope(client, world, as_user):
    response = client.get(f"{BASE}/events?institution_id={world.inst['B']}", headers=as_user("admin_a"))
    assert response.status_code == 403 and response.get_json()["details"]["reason"] == "out_of_scope"
    own = client.get(f"{BASE}/events?institution_id={world.inst['A']}", headers=as_user("admin_a"))
    assert own.status_code == 200
    assert client.get(f"{BASE}/events/{world.events['foreign']}", headers=as_user("admin_a")).status_code == 404
    assert client.get(f"{BASE}/events/{world.events['foreign']}", headers=as_user("admin_b")).status_code == 200


# --- Detail and representation ---------------------------------------------------------------------


def test_access_audit_event_detail(client, world, as_user):
    item = client.get(f"{BASE}/events/{world.events['family']}", headers=as_user("admin_a")).get_json()
    assert item["record_type"] == "access_audit" and item["category"] == "business" and item["source_service"] == "catalog"
    assert item["action"] == "CREATE_INSTRUMENT_FAMILY" and item["resource"]["label"] == f"ZZ-AUD-{world.tag}"
    assert item["actor"]["email"] == f"admin_a.{world.tag}@audit-test.invalid" and item["actor"]["role_codes"] == ["it_admin"]
    assert item["correlation_id"] == "corr-fam" and item["ip"] == "10.1.2.3" and item["details"] is None


def test_count_event_detail_never_leaks_boxes_or_free_text(client, world, as_user):
    initial = client.get(f"{BASE}/events/{world.events['initial']}", headers=as_user("spd_a")).get_json()
    assert initial["record_type"] == "count_event" and initial["category"] == "operation" and initial["actor"] is None
    assert initial["session_id"] == str(world.session) and initial["details"]["trigger"] == "initial"
    assert initial["details"]["detected_quantity"] == 5 and initial["source_service"] == "web"
    manual = client.get(f"{BASE}/events/{world.events['manual']}", headers=as_user("spd_a"))
    text_body = manual.get_data(as_text=True) + str(initial)
    for secret in ("boxes", "run-1", "free text", "Operator note", "reason_code"):
        assert secret not in text_body
    blocked = client.get(f"{BASE}/events/{world.events['blocked']}", headers=as_user("spd_a")).get_json()
    assert blocked["outcome"] == "blocked" and blocked["details"]["reason"] == "final_count_mismatch"


@pytest.mark.parametrize("raw", ["not-a-uuid", str(uuid.uuid4())])
def test_unknown_event_is_404(client, world, as_user, raw):
    response = client.get(f"{BASE}/events/{raw}", headers=as_user("admin_a"))
    assert response.status_code == 404 and response.get_json()["error"] == "not_found"


def test_supervisor_cannot_read_identity_events(client, world, as_user):
    assert client.get(f"{BASE}/events/{world.events['user_admin']}", headers=as_user("spd_a")).status_code == 404
    assert client.get(f"{BASE}/events/{world.events['login']}", headers=as_user("spd_a")).status_code == 404


# --- Filters, pagination, sort -------------------------------------------------------------------------


@pytest.mark.parametrize("query, expected", [
    ("user_id={op_a}", ["login", "start", "open", "blocked", "manual", "denied"]),
    ("session_id={session}", ["scheduled", "start", "open", "initial", "blocked", "manual", "denied"]),
    ("category=security", ["login", "denied", "user_admin"]),
    ("category=operation", ["start", "open", "initial", "blocked", "manual"]),
    ("outcome=denied", ["denied"]),
    ("outcome=blocked", ["blocked"]),
    ("source_service=auth", ["login", "user_admin"]),
    ("source_service=session", ["scheduled", "denied"]),
    ("record_type=count_event", ["open", "initial", "blocked", "manual"]),
    ("action=session.start", ["start"]),
    ("resource_type=instrument_family", ["family"]),
    ("correlation_id=corr-sched", ["scheduled"]),
    ("from=2026-10-20T12:05:00Z&to=2026-10-20T12:07:00Z", ["blocked", "manual"]),
    ("q=CREATE_INSTRUMENT", ["family"]),
])
def test_filters(client, world, as_user, query, expected):
    query = query.format(op_a=world.users["op_a"].id, session=world.session)
    response = client.get(f"{BASE}/events?per_page=100&{query}", headers=as_user("admin_a"))
    assert response.status_code == 200, response.get_data(as_text=True)
    assert set(scoped(world, response)) == {str(world.events[k]) for k in expected}


def test_pagination_and_sort(client, world, as_user):
    query = f"session_id={world.session}&per_page=3"
    first = client.get(f"{BASE}/events?{query}&page=1&sort=occurred_at", headers=as_user("admin_a")).get_json()
    second = client.get(f"{BASE}/events?{query}&page=2&sort=occurred_at", headers=as_user("admin_a")).get_json()
    last = client.get(f"{BASE}/events?{query}&page=3&sort=occurred_at", headers=as_user("admin_a")).get_json()
    assert first["total"] == 7 and first["total_pages"] == 3 and first["sort"] == "occurred_at"
    assert [len(p["items"]) for p in (first, second, last)] == [3, 3, 1]
    assert first["items"][0]["id"] == str(world.events["scheduled"])
    all_ids = [i["id"] for p in (first, second, last) for i in p["items"]]
    assert len(set(all_ids)) == 7


@pytest.mark.parametrize("query, field", [
    ("page=0", "page"), ("per_page=101", "per_page"), ("per_page=x", "page"), ("sort=name", "sort"),
    ("category=other", "category"), ("outcome=ok", "outcome"), ("user_id=nope", "user_id"),
    ("session_id=1", "session_id"), ("from=yesterday", "from"), ("from=2026-10-21&to=2026-10-20", "to"),
    ("correlation_id=a%20b", "correlation_id"), ("source_service=mongo", "source_service"),
])
def test_invalid_parameters_are_400(client, world, as_user, query, field):
    response = client.get(f"{BASE}/events?{query}", headers=as_user("admin_a"))
    assert response.status_code == 400 and response.get_json()["details"]["field"] == field


def test_unknown_parameters_are_400(client, world, as_user):
    response = client.get(f"{BASE}/events?institution=x&limit=5", headers=as_user("admin_a"))
    assert response.status_code == 400
    assert response.get_json()["details"]["unknown_parameters"] == ["institution", "limit"]


# --- Summary ---------------------------------------------------------------------------------------------


def test_summary_counts_the_same_scope(client, world, as_user):
    data = client.get(f"{BASE}/events/summary?session_id={world.session}", headers=as_user("spd_a")).get_json()
    assert data["total"] == 7
    assert {g["value"]: g["count"] for g in data["by_record_type"]} == {"access_audit": 3, "count_event": 4}
    assert {g["value"]: g["count"] for g in data["by_outcome"]} == {"success": 5, "denied": 1, "blocked": 1}
    assert data["by_session"][0]["session_id"] == str(world.session) and data["by_session"][0]["count"] == 7
    assert all(user["email"] is None for user in data["by_user"])
    assert data["first_at"] == (NOW + timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    assert client.get(f"{BASE}/events/summary?page=1", headers=as_user("spd_a")).status_code == 400


# --- Formats and contract ----------------------------------------------------------------------------------


def test_xml_documents_conform_to_the_xsd(client, world, as_user):
    for path in (f"/events?session_id={world.session}", f"/events/{world.events['manual']}",
                 f"/events/{world.events['family']}", "/events/summary", "/events?page=0", f"/events/{uuid.uuid4()}"):
        response = client.get(f"{BASE}{path}", headers=as_user("admin_a", **XML))
        document = etree.fromstring(response.data)
        assert XSD.validate(document), (path, XSD.error_log)
    page = etree.fromstring(client.get(f"{BASE}/events?session_id={world.session}", headers=as_user("admin_a", **XML)).data)
    assert page.tag == "event_page" and len(page.findall("items/event")) == 7
    assert XSD.validate(etree.fromstring(client.get("/health", headers=XML).data))


def test_json_and_xml_carry_the_same_event(client, world, as_user):
    js = client.get(f"{BASE}/events/{world.events['scheduled']}", headers=as_user("admin_a")).get_json()
    xml = etree.fromstring(client.get(f"{BASE}/events/{world.events['scheduled']}", headers=as_user("admin_a", **XML)).data)
    assert xml.findtext("id") == js["id"] and xml.findtext("action") == js["action"]
    assert xml.findtext("resource/label") == js["resource"]["label"] and xml.findtext("source_service") == "session"


def test_contract_documents_and_health(client):
    assert b"/api/v1/audit/events/summary" in client.get(f"{BASE}/openapi.yaml").data
    assert client.get(f"{BASE}/schemas/audit.xsd").status_code == 200
    assert b"swagger-ui" in client.get(f"{BASE}/docs").data
    health = client.get("/health")
    assert health.status_code == 200 and health.get_json()["checks"] == {"database": "ok", "redis": "ok"}


def test_correlation_id_is_echoed(client, world, as_user):
    response = client.get(f"{BASE}/events", headers=as_user("admin_a", **{"X-Correlation-ID": "corr-audit-1"}))
    assert response.headers["X-Correlation-ID"] == "corr-audit-1"
    bad = client.get(f"{BASE}/events?page=0", headers=as_user("admin_a", **{"X-Correlation-ID": "x" * 65}))
    assert bad.get_json()["correlation_id"] == bad.headers["X-Correlation-ID"] != "x" * 65


def test_openapi_xml_example_conforms_to_the_xsd():
    import yaml

    spec = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text(encoding="utf-8"))
    example = spec["paths"]["/api/v1/audit/events"]["get"]["responses"]["200"]["content"]["application/xml"]["example"]
    assert XSD.validate(etree.fromstring(example.strip().encode())), XSD.error_log
