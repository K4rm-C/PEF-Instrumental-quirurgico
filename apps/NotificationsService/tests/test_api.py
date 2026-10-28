"""/api/v1/notifications — own notifications only, read state, filters, formats, security."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from lxml import etree
from sqlalchemy import text

BASE = "/api/v1/notifications"
XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / "schemas" / "notifications.xsd")))
XML = {"Accept": "application/xml"}


@pytest.fixture
def produced(world, worker):
    world.count("manual_close", world.session, "op_a", {"next_status": "correction_required", "open_discrepancies": 1})
    world.audit("discrepancy.resolve", world.session, "spd_a")
    worker.drain()
    return world


def own_ids(client, headers, **params):
    return [item["id"] for item in client.get(BASE, query_string=params, headers=headers).get_json()["items"]]


def test_the_operator_sees_only_their_notifications(client, produced, as_user):
    page = client.get(BASE, headers=as_user("op_a")).get_json()
    assert {item["type"] for item in page["items"]} == {"session_scheduled", "correction_required", "discrepancy_resolved"}
    assert page["unread"] == 3 and page["total"] == 3
    item = next(i for i in page["items"] if i["type"] == "correction_required")
    assert item["status"] == "unread" and item["read_at"] is None and item["session_id"] == str(produced.session)
    assert "WS-" in item["message"] and set(item["data"]) == {"session_label", "open_discrepancies"}
    supervisor = client.get(BASE, headers=as_user("spd_a")).get_json()
    assert [i["type"] for i in supervisor["items"]] == ["discrepancy_pending"]
    assert client.get(BASE, headers=as_user("spd_a2")).get_json()["total"] == 0


def test_mark_read_is_idempotent_and_persisted(client, produced, as_user, db_conn):
    headers = as_user("op_a")
    target = own_ids(client, headers, type="session_scheduled")[0]
    first = client.patch(f"{BASE}/{target}/read", headers=headers).get_json()
    second = client.patch(f"{BASE}/{target}/read", headers=headers).get_json()
    assert first["status"] == "read" and first["read_at"] and second["read_at"] == first["read_at"]
    row = db_conn.execute(text("SELECT status, read_at FROM notification WHERE id = :id"), {"id": target}).one()
    assert row[0] == "read" and row[1] is not None
    assert client.get(f"{BASE}/unread-count", headers=headers).get_json() == {"unread": 2}
    assert own_ids(client, headers, status="read") == [target]


def test_another_user_cannot_read_or_mark_a_notification(client, produced, as_user, db_conn):
    target = own_ids(client, as_user("op_a"), type="session_scheduled")[0]
    for user in ("spd_a", "admin_a", "op_b"):
        assert client.get(f"{BASE}/{target}", headers=as_user(user)).status_code == 404
        assert client.patch(f"{BASE}/{target}/read", headers=as_user(user)).status_code == 404
    assert db_conn.execute(text("SELECT status FROM notification WHERE id = :id"), {"id": target}).scalar_one() == "unread"


def test_institution_isolation_even_for_the_same_user(client, produced, as_user, db_conn):
    headers = as_user("op_a")
    target = own_ids(client, headers)[0]
    # The user moves to institution B: notifications of A are no longer theirs to see.
    db_conn.execute(text('UPDATE "user" SET institution_id = :b WHERE id = :u'),
                    {"b": produced.inst["B"], "u": produced.users["op_a"].id})
    from conftest import TokenFactory  # noqa: F401
    moved = {"Authorization": headers["Authorization"]}
    assert client.get(BASE, headers=moved).status_code == 401   # the Redis session is bound to institution A


def test_read_all_json_and_xml_with_validation(client, produced, as_user):
    headers = as_user("op_a")
    result = client.patch(f"{BASE}/read-all", json={"type": "correction_required"}, headers=headers).get_json()
    assert result == {"updated": 1, "unread": 2}
    xml_body = b"<read_all_request><type>discrepancy_resolved</type></read_all_request>"
    response = client.patch(f"{BASE}/read-all", data=xml_body,
                            headers={**headers, "Content-Type": "application/xml", "Accept": "application/xml"})
    document = etree.fromstring(response.data)
    assert response.status_code == 200 and XSD.validate(document) and document.findtext("updated") == "1"
    everything = client.patch(f"{BASE}/read-all", headers=headers).get_json()
    assert everything == {"updated": 1, "unread": 0}
    again = client.patch(f"{BASE}/read-all", headers=headers).get_json()
    assert again == {"updated": 0, "unread": 0}


@pytest.mark.parametrize("body, content_type", [
    (b"<read_all_request><type>party</type></read_all_request>", "application/xml"),
    (b"<read_all_request><kind>x</kind></read_all_request>", "application/xml"),
    (b"<other/>", "application/xml"),
    (b'<!DOCTYPE x [<!ENTITY e "x">]><read_all_request/>', "application/xml"),
    (b'{"type": "party"}', "application/json"),
    (b'{"recipient_user_id": "x"}', "application/json"),
    (b"[1]", "application/json"),
    (b"type=x", "text/plain"),
])
def test_invalid_read_all_bodies_are_400(client, produced, as_user, body, content_type):
    response = client.patch(f"{BASE}/read-all", data=body, headers={**as_user("op_a"), "Content-Type": content_type})
    assert response.status_code == 400 and response.get_json()["error"] == "invalid_request"
    assert client.get(f"{BASE}/unread-count", headers=as_user("op_a")).get_json()["unread"] == 3


def test_xsd_failure_details(client, produced, as_user):
    response = client.patch(f"{BASE}/read-all", data=b"<read_all_request><type>party</type></read_all_request>",
                            headers={**as_user("op_a"), "Content-Type": "application/xml"})
    assert response.get_json()["details"]["reason"] == "xsd_validation_failed"


@pytest.mark.parametrize("query, field", [
    ("status=done", "status"), ("type=x", "type"), ("page=0", "page"), ("per_page=500", "per_page"),
    ("from=yesterday", "from"), ("sort=type", "sort"), ("from=2026-10-22&to=2026-10-21", "to"),
])
def test_invalid_parameters_are_400(client, produced, as_user, query, field):
    response = client.get(f"{BASE}?{query}", headers=as_user("op_a"))
    assert response.status_code == 400 and response.get_json()["details"]["field"] == field


def test_unknown_parameters_and_ids(client, produced, as_user):
    assert client.get(f"{BASE}?recipient=me", headers=as_user("op_a")).get_json()["details"]["unknown_parameters"] == ["recipient"]
    for raw in ("nope", str(uuid.uuid4())):
        assert client.get(f"{BASE}/{raw}", headers=as_user("op_a")).status_code == 404
        assert client.patch(f"{BASE}/{raw}/read", headers=as_user("op_a")).status_code == 404


def test_filters_and_pagination(client, produced, as_user):
    headers = as_user("op_a")
    first = client.get(f"{BASE}?per_page=2&page=1&sort=created_at", headers=headers).get_json()
    second = client.get(f"{BASE}?per_page=2&page=2&sort=created_at", headers=headers).get_json()
    assert first["total"] == 3 and first["total_pages"] == 2 and len(first["items"]) == 2 and len(second["items"]) == 1
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    assert len(own_ids(client, headers, type="discrepancy_resolved")) == 1
    assert len(own_ids(client, headers, **{"from": "2000-01-01", "to": "2100-01-01"})) == 3


def test_no_creation_or_deletion_endpoint(client, produced, as_user):
    headers = as_user("admin_a")
    assert client.post(BASE, json={"type": "session_scheduled"}, headers=headers).status_code == 405
    target = own_ids(client, as_user("op_a"))[0]
    assert client.delete(f"{BASE}/{target}", headers=headers).status_code == 405


def test_tokens_invalid_expired_revoked_unregistered_or_headers_are_401(client, produced, tokens):
    user = produced.users["op_a"]
    for headers in ({}, {"Authorization": "Bearer abc"},
                    {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id, lifetime=timedelta(seconds=-5))}"},
                    {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id, revoked=True)}"},
                    {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id, store=False)}"},
                    {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id, secret='x' * 40)}"},
                    {"X-User-Id": str(user.id)}):
        response = client.get(BASE, headers=headers)
        assert response.status_code == 401 and response.get_json()["error"] == "unauthorized"


def test_xml_responses_conform_to_the_xsd(client, produced, as_user):
    headers = as_user("op_a", **XML)
    page = etree.fromstring(client.get(BASE, headers=headers).data)
    assert XSD.validate(page), XSD.error_log
    target = page.find("items/notification/id").text
    for path in (f"/{target}", "/unread-count", f"/{uuid.uuid4()}", "?page=0"):
        assert XSD.validate(etree.fromstring(client.get(f"{BASE}{path}", headers=headers).data)), path
    assert XSD.validate(etree.fromstring(client.patch(f"{BASE}/{target}/read", headers=headers).data))
    assert XSD.validate(etree.fromstring(client.get("/health", headers=XML).data))


def test_contract_documents_health_and_backlog(client):
    assert b"/api/v1/notifications/read-all" in client.get(f"{BASE}/openapi.yaml").data
    assert client.get(f"{BASE}/schemas/notifications.xsd").status_code == 200
    assert b"swagger-ui" in client.get(f"{BASE}/docs").data
    health = client.get("/health")
    assert health.status_code == 200 and "X-Outbox-Pending" in health.headers
