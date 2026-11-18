"""GET /api/v1/sessions/{id} — DS09 Session detail."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

import pytest

BASE = "/api/v1/sessions"
DETAIL_FIELDS = ["id", "operation_id", "user_id", "station_id", "kit_id", "status_code", "capture_mode",
                 "current_phase_code", "scheduled_at", "started_at", "ended_at", "closed_by_user_id",
                 "open_discrepancies", "privacy_agreement"]
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def test_scheduled_session_detail(client, world, as_user):
    s = world.sessions["a1"]
    response = client.get(f"{BASE}/{s.id}", headers=as_user("spd_a"))
    assert response.status_code == 200
    body = response.get_json()
    assert list(body) == DETAIL_FIELDS
    assert body == {
        "id": str(s.id), "operation_id": str(s.operation_id), "user_id": str(world.users["op_a"].id),
        "station_id": str(world.stations["A"]), "kit_id": str(world.kits["A"]), "status_code": "scheduled",
        "capture_mode": None, "current_phase_code": "start", "scheduled_at": "2026-10-03T12:00:00Z",
        "started_at": None, "ended_at": None, "closed_by_user_id": None, "open_discrepancies": 0,
        "privacy_agreement": False,
    }


def test_in_progress_vision_session_with_agreement(client, world, as_user):
    body = client.get(f"{BASE}/{world.sessions['a2'].id}", headers=as_user("op_a2")).get_json()
    assert body["status_code"] == "in_progress"
    assert body["capture_mode"] == "vision"
    assert body["started_at"] == "2026-10-01T10:00:00Z"
    assert body["privacy_agreement"] is True


def test_open_discrepancies_count(client, world, as_user):
    body = client.get(f"{BASE}/{world.sessions['a3'].id}", headers=as_user("op_a")).get_json()
    assert body["open_discrepancies"] == 2   # resolved ones are not counted


def test_null_phase_and_scheduled_at(client, world, as_user):
    body = client.get(f"{BASE}/{world.sessions['a_unscheduled'].id}", headers=as_user("spd_a")).get_json()
    assert body["current_phase_code"] is None and body["scheduled_at"] is None


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "123", "zzzzzzzz-0000-4000-8000-000000000000"])
def test_invalid_uuid_is_404(client, world, as_user, bad_id):
    response = client.get(f"{BASE}/{bad_id}", headers=as_user("spd_a"))
    assert response.status_code == 404 and response.get_json()["error"] == "not_found"


def test_nonexistent_is_404(client, world, as_user):
    assert client.get(f"{BASE}/{uuid.uuid4()}", headers=as_user("spd_a")).status_code == 404


def test_operator_not_assigned_is_404(client, world, as_user):
    response = client.get(f"{BASE}/{world.sessions['a2'].id}", headers=as_user("op_a"))
    assert response.status_code == 404


def test_cross_tenant_and_unknown_are_indistinguishable(client, world, as_user):
    headers = as_user("spd_a")
    foreign = client.get(f"{BASE}/{world.sessions['b1'].id}", headers=headers).get_json()
    unknown = client.get(f"{BASE}/{uuid.uuid4()}", headers=headers).get_json()
    foreign.pop("correlation_id"), unknown.pop("correlation_id")
    assert foreign == unknown == {"error": "not_found", "message": "Session not found", "details": {}}


def test_detail_xml_matches_json(client, world, as_user):
    path = f"{BASE}/{world.sessions['a1'].id}"
    as_json = client.get(path, headers=as_user("spd_a")).get_json()
    root = ET.fromstring(client.get(path, headers=as_user("spd_a", Accept="application/xml")).data)
    assert root.tag == "session"
    assert [c.tag for c in root] == DETAIL_FIELDS
    for field, value in as_json.items():
        node = root.find(field)
        if value is None:
            assert node.get(XSI_NIL) == "true", field
        elif isinstance(value, bool):
            assert node.text == ("true" if value else "false")
        else:
            assert node.text == str(value), field
