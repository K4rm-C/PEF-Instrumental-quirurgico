"""GET /api/v1/sessions — DS09 Session List Item, role scope, ordering."""

from __future__ import annotations

import xml.etree.ElementTree as ET

LIST = "/api/v1/sessions"
LIST_FIELDS = ["id", "status_code", "capture_mode", "scheduled_at", "started_at", "ended_at", "user_id", "kit_id",
               "open_discrepancies"]
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def _ids(response):
    return [item["id"] for item in response.get_json()]


def test_supervisor_sees_all_tenant_sessions_in_order(client, world, as_user):
    response = client.get(LIST, headers=as_user("spd_a"))
    assert response.status_code == 200
    s = world.sessions
    # scheduled_at DESC (a3 +3d, a1 +2d, a2 +1d), sessions without scheduled_at last.
    assert _ids(response) == [str(s[k].id) for k in ("a3", "a1", "a2", "a_unscheduled")]


def test_list_item_fields_and_values(client, world, as_user):
    items = {item["id"]: item for item in client.get(LIST, headers=as_user("spd_a")).get_json()}
    for item in items.values():
        assert list(item) == LIST_FIELDS
    a1 = items[str(world.sessions["a1"].id)]
    assert a1 == {
        "id": str(world.sessions["a1"].id), "status_code": "scheduled", "capture_mode": None,
        "scheduled_at": "2026-10-03T12:00:00Z", "started_at": None, "ended_at": None,
        "user_id": str(world.users["op_a"].id), "kit_id": str(world.kits["A"]), "open_discrepancies": 0,
    }
    a3 = items[str(world.sessions["a3"].id)]
    assert a3["status_code"] == "correction_required" and a3["open_discrepancies"] == 2
    assert a3["capture_mode"] == "manual_no_privacy" and a3["started_at"] is not None


def test_operator_sees_only_assigned_sessions(client, world, as_user):
    s = world.sessions
    assert _ids(client.get(LIST, headers=as_user("op_a"))) == [str(s["a3"].id), str(s["a1"].id)]
    assert _ids(client.get(LIST, headers=as_user("op_a2"))) == [str(s["a2"].id), str(s["a_unscheduled"].id)]


def test_multi_role_user_gets_supervisor_scope(client, world, as_user):
    assert len(client.get(LIST, headers=as_user("multi_a")).get_json()) == 4


def test_no_cross_tenant_or_operationless_sessions(client, world, as_user):
    ids_a = set(_ids(client.get(LIST, headers=as_user("spd_a"))))
    assert str(world.sessions["b1"].id) not in ids_a
    assert str(world.sessions["no_operation"].id) not in ids_a
    assert str(world.sessions["no_operation"].id) not in _ids(client.get(LIST, headers=as_user("op_a")))
    assert _ids(client.get(LIST, headers=as_user("spd_b"))) == [str(world.sessions["b1"].id)]


def test_empty_list(client, world, as_user):
    response = client.get(LIST, headers=as_user("op_admin_a"))
    assert response.status_code == 200 and response.get_json() == []
    xml = client.get(LIST, headers=as_user("op_admin_a", Accept="application/xml"))
    root = ET.fromstring(xml.data)
    assert root.tag == "sessions" and len(root) == 0


def test_list_xml(client, world, as_user):
    response = client.get(LIST, headers=as_user("spd_a", Accept="application/xml"))
    assert response.status_code == 200 and response.mimetype == "application/xml"
    root = ET.fromstring(response.data)
    assert root.tag == "sessions"
    assert [child.tag for child in root] == ["session"] * 4
    assert [c.tag for c in root[0]] == LIST_FIELDS
    a1 = [node for node in root if node.findtext("id") == str(world.sessions["a1"].id)][0]
    assert a1.find("capture_mode").get(XSI_NIL) == "true"
    assert a1.findtext("open_discrepancies") == "0"


def test_query_parameters_do_not_change_scope(client, world, as_user):
    url = f"{LIST}?institution_id={world.institutions['B']}&user_id={world.users['op_b'].id}&page=2&status=closed"
    assert len(client.get(url, headers=as_user("spd_a")).get_json()) == 4


def test_list_has_correlation_id(client, world, as_user):
    response = client.get(LIST, headers=as_user("spd_a", **{"X-Correlation-ID": "corr-list-1"}))
    assert response.headers["X-Correlation-ID"] == "corr-list-1"


def test_endpoints_outside_session_api_v1_do_not_exist(client, world, as_user):
    """DS09: abort, phase change and PUT /sessions/{id} are outside Session API v1."""
    session_id = world.sessions["a2"].id
    for path in (f"{LIST}/{session_id}/abort", f"{LIST}/{session_id}/phase", f"{LIST}/{session_id}/phase-change"):
        assert client.post(path, headers=as_user("multi_a"), json={}).status_code in (404, 405), path
    assert client.delete(f"{LIST}/{session_id}", headers=as_user("spd_a")).status_code == 405
    # PUT /sessions/{id} is the full edit of a scheduled session (DS09 §1.3.4 complement v0.2.0).
    assert client.put(f"{LIST}/{session_id}", headers=as_user("spd_a"), json={}).status_code in (400, 409, 422)
