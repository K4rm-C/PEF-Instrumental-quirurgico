"""POST /api/v1/sessions/{id}/vision-counts (Phase D)."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import text

from count_fixtures import cdata, set_status, vision_body  # noqa: F401  (fixture)

BASE = "/api/v1/sessions"


def _url(session_id):
    return f"{BASE}/{session_id}/vision-counts"


def _auto_counts(db_conn, session_id):
    return db_conn.execute(text(
        "SELECT e.id, e.client_event_id, e.family_id, e.expected_quantity, e.detected_quantity, e.payload, "
        "e.occurred_at, e.user_id FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
        "WHERE e.session_id = :s AND t.code = 'auto_count' ORDER BY e.occurred_at, e.id"), {"s": session_id}).mappings().all()


def _audits(db_conn, session_id):
    return db_conn.execute(text("SELECT action FROM access_audit WHERE resource_id = :s AND outcome = 'success'"), {"s": session_id}).all()


def _post(client, as_user, session_id, body, user="op_a", **headers):
    return client.post(_url(session_id), json=body, headers=as_user(user, **headers))


# --- Persistence -------------------------------------------------------------------------------


def test_final_batch_is_one_auto_count(client, world, cdata, as_user, db_conn):
    body = vision_body(world, ("b", 1), ("a", 2), occurred_at="2026-10-14T07:00:00+02:00")
    response = _post(client, as_user, cdata.vision, body, **{"X-Correlation-ID": "corr-vision-1"})
    assert response.status_code == 201, response.get_data(as_text=True)
    result = response.get_json()
    rows = _auto_counts(db_conn, cdata.vision)
    assert len(rows) == 1
    row = rows[0]
    assert result == {"count_event_id": str(row["id"]), "client_event_id": body["client_event_id"],
                      "trigger": "final", "occurred_at": "2026-10-14T05:00:00Z"}
    assert response.headers["X-Correlation-ID"] == "corr-vision-1"
    assert row["user_id"] is None and row["family_id"] is None             # model fact, not the operator's
    assert str(row["client_event_id"]) == body["client_event_id"]
    assert (row["expected_quantity"], row["detected_quantity"]) == (6, 3)  # batch totals (DS04)
    assert row["payload"] == {"model_version": "yolo26l-demo", "trigger": "final", "counts": sorted(
        [{"family_id": str(world.families["a"]), "detected_quantity": 2},
         {"family_id": str(world.families["b"]), "detected_quantity": 1}], key=lambda item: item["family_id"])}
    assert _audits(db_conn, cdata.vision) == []        # the fact is the auto_count itself


def test_stable_window_is_accepted(client, world, cdata, as_user, db_conn):
    response = _post(client, as_user, cdata.vision, vision_body(world, ("a", 1), trigger="stable_window"))
    assert response.status_code == 201 and response.get_json()["trigger"] == "stable_window"
    assert _auto_counts(db_conn, cdata.vision)[0]["payload"]["trigger"] == "stable_window"


def test_zero_and_partial_and_empty_counts_are_valid(client, world, cdata, as_user, db_conn):
    """Not every expected family has to appear; an empty batch (nothing detected) is a valid fact."""
    assert _post(client, as_user, cdata.vision, vision_body(world, ("a", 0))).status_code == 201
    assert _post(client, as_user, cdata.vision, vision_body(world)).status_code == 201
    assert [r["payload"]["counts"] for r in _auto_counts(db_conn, cdata.vision)] in (
        [[{"family_id": str(world.families["a"]), "detected_quantity": 0}], []],
        [[], [{"family_id": str(world.families["a"]), "detected_quantity": 0}]])


def test_family_deactivated_after_scheduling_is_still_accepted(client, world, cdata, as_user, db_conn):
    db_conn.execute(text("UPDATE instrument_family SET active = FALSE WHERE id = :f"), {"f": world.families["a"]})
    assert _post(client, as_user, cdata.vision, vision_body(world, ("a", 2))).status_code == 201


def test_xml_request_and_response(client, world, cdata, as_user, db_conn):
    client_event_id = uuid.uuid4()
    xml = (
        "<vision_counts>"
        f"<client_event_id>{client_event_id}</client_event_id>"
        "<occurred_at>2026-10-14T05:00:00Z</occurred_at><model_version>yolo26l-demo</model_version>"
        "<trigger>final</trigger><counts>"
        f"<count><family_id>{world.families['a']}</family_id><detected_quantity>2</detected_quantity></count>"
        f"<count><family_id>{world.families['c']}</family_id><detected_quantity>0</detected_quantity></count>"
        "</counts></vision_counts>"
    )
    headers = as_user("op_a", **{"Content-Type": "application/xml", "Accept": "application/xml"})
    response = client.post(_url(cdata.vision), data=xml, headers=headers)
    assert response.status_code == 201, response.get_data(as_text=True)
    root = ET.fromstring(response.data)
    assert root.tag == "vision_count_result"
    assert [child.tag for child in root] == ["count_event_id", "client_event_id", "trigger", "occurred_at"]
    assert root.findtext("client_event_id") == str(client_event_id)
    replay = client.post(_url(cdata.vision), data=xml, headers=headers)
    assert replay.status_code == 200 and ET.fromstring(replay.data).findtext("count_event_id") == root.findtext("count_event_id")


def test_xml_empty_counts(client, world, cdata, as_user):
    xml = (f"<vision_counts><client_event_id>{uuid.uuid4()}</client_event_id>"
           "<occurred_at>2026-10-14T05:00:00Z</occurred_at><model_version>m</model_version>"
           "<trigger>final</trigger><counts/></vision_counts>")
    response = client.post(_url(cdata.vision), data=xml, headers=as_user("op_a", **{"Content-Type": "application/xml"}))
    assert response.status_code == 201


def test_xml_error_response(client, world, cdata, as_user):
    response = client.post(_url(cdata.vision), data="<vision_counts><boxes>1</boxes></vision_counts>",
                           headers=as_user("op_a", **{"Content-Type": "application/xml", "Accept": "application/xml",
                                                      "X-Correlation-ID": "corr-vx"}))
    root = ET.fromstring(response.data)
    assert response.status_code == 400 and root.tag == "error_response"
    assert root.findtext("error") == "invalid_request" and root.findtext("correlation_id") == "corr-vx"


def test_get_counts_reads_the_new_batch_without_payload(client, world, cdata, as_user):
    body = vision_body(world, ("a", 2), ("b", 1))
    event_id = _post(client, as_user, cdata.vision, body).get_json()["count_event_id"]
    counts = client.get(f"{BASE}/{cdata.vision}/counts", headers=as_user("op_a")).get_json()
    item = next(c for c in counts if c["id"] == event_id)
    assert item["event_type_code"] == "auto_count" and item["user_id"] is None
    assert item["model_version"] == "yolo26l-demo" and item["trigger"] == "final"
    assert item["client_event_id"] == body["client_event_id"]
    assert sorted(item["counts"], key=lambda c: c["family_id"]) == sorted(body["counts"], key=lambda c: c["family_id"])
    assert "payload" not in item and "boxes" not in str(item)


# --- Access ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("user_key, target, status", [
    ("op_a2", "vision", 404),      # other operator of the same tenant
    ("op_b", "vision", 404),       # operator of another tenant
    ("op_a", "vision_b", 404),     # session of another tenant
    ("spd_a", "vision", 403),      # supervisor without station_operator
    ("admin_a", "vision", 403),
    ("multi_a", "vision", 404),    # has station_operator but is not the assigned operator
])
def test_access_rules(client, world, cdata, as_user, db_conn, user_key, target, status):
    session_id = getattr(cdata, target)
    assert _post(client, as_user, session_id, vision_body(world, ("a", 1)), user=user_key).status_code == status
    assert _auto_counts(db_conn, session_id) == []


def test_assigned_multi_role_operator_is_allowed(client, world, cdata, as_user, db_conn):
    db_conn.execute(text("UPDATE work_session SET user_id = :u WHERE id = :s"),
                    {"u": world.users["multi_a"].id, "s": cdata.vision})
    assert _post(client, as_user, cdata.vision, vision_body(world, ("a", 1)), user="multi_a").status_code == 201


def test_requires_token(client, world, cdata):
    assert client.post(_url(cdata.vision), json=vision_body(world, ("a", 1))).status_code == 401


@pytest.mark.parametrize("bad_id", ["not-a-uuid", str(uuid.uuid4())])
def test_invalid_or_unknown_session_is_404(client, world, cdata, as_user, bad_id):
    assert _post(client, as_user, bad_id, vision_body(world, ("a", 1))).status_code == 404


@pytest.mark.parametrize("status", ["scheduled", "awaiting_spd_review", "correction_required", "closed", "aborted"])
def test_non_in_progress_is_409(client, world, cdata, as_user, db_conn, status):
    set_status(db_conn, cdata.vision, status)
    response = _post(client, as_user, cdata.vision, vision_body(world, ("a", 1)))
    assert response.status_code == 409 and response.get_json()["details"] == {"status_code": status}
    assert _auto_counts(db_conn, cdata.vision) == []


def test_manual_session_is_409(client, world, cdata, as_user, db_conn):
    response = _post(client, as_user, cdata.manual, vision_body(world, ("a", 1)))
    assert response.status_code == 409 and response.get_json()["details"] == {"capture_mode": "manual_no_privacy"}
    assert _auto_counts(db_conn, cdata.manual) == []


# --- Payload validation ----------------------------------------------------------------------------


def _mutate(world, **changes):
    body = vision_body(world, ("a", 1))
    for key, value in changes.items():
        if value is ...:
            body.pop(key)
        else:
            body[key] = value
    return body


@pytest.mark.parametrize("changes", [
    {"trigger": "video_window"}, {"trigger": "FINAL"}, {"trigger": None},
    {"client_event_id": ...}, {"client_event_id": "not-a-uuid"}, {"client_event_id": 5},
    {"occurred_at": ...}, {"occurred_at": "yesterday"}, {"occurred_at": "2026-10-14T05:00:00"},   # no timezone
    {"occurred_at": "2026-10-14"}, {"occurred_at": 1760418000},
    {"model_version": ...}, {"model_version": ""}, {"model_version": "   "}, {"model_version": "m" * 33},
    {"model_version": 26},
    {"counts": ...}, {"counts": None}, {"counts": {"a": 1}},
])
def test_invalid_top_level_fields_are_400(client, world, cdata, as_user, db_conn, changes):
    response = _post(client, as_user, cdata.vision, _mutate(world, **changes))
    assert response.status_code == 400, response.get_data(as_text=True)
    assert response.get_json()["error"] == "invalid_request"
    assert _auto_counts(db_conn, cdata.vision) == []


def test_model_version_of_32_chars_is_valid(client, world, cdata, as_user):
    assert _post(client, as_user, cdata.vision, _mutate(world, model_version="m" * 32)).status_code == 201


@pytest.mark.parametrize("quantity", [-1, True, False, "2", 2.0, 2.5, 32768, None])
def test_invalid_detected_quantity_is_400(client, world, cdata, as_user, db_conn, quantity):
    body = _mutate(world, counts=[{"family_id": str(world.families["a"]), "detected_quantity": quantity}])
    assert _post(client, as_user, cdata.vision, body).status_code == 400
    assert _auto_counts(db_conn, cdata.vision) == []


def test_duplicate_family_is_400(client, world, cdata, as_user):
    body = vision_body(world, ("a", 1), ("a", 2))
    response = _post(client, as_user, cdata.vision, body)
    assert response.status_code == 400 and response.get_json()["details"] == {"family_id": str(world.families["a"])}


@pytest.mark.parametrize("family", ["d", "unknown"])
def test_family_outside_expected_inventory_is_422(client, world, cdata, as_user, db_conn, family):
    family_id = world.families["d"] if family == "d" else uuid.uuid4()
    body = _mutate(world, counts=[{"family_id": str(family_id), "detected_quantity": 1}])
    response = _post(client, as_user, cdata.vision, body)
    assert response.status_code == 422 and response.get_json()["error"] == "invalid_reference"
    assert _auto_counts(db_conn, cdata.vision) == []


def test_family_of_other_tenant_session_reveals_nothing(client, world, cdata, as_user):
    """vision_b (tenant B) expects family a; for A's session only A's inventory matters."""
    body = _mutate(world, counts=[{"family_id": str(world.families["d"]), "detected_quantity": 1}])
    assert _post(client, as_user, cdata.vision, body).get_json()["message"] == \
        "Family is not part of the session's expected inventory"


@pytest.mark.parametrize("field", ["boxes", "frames", "video_t", "run_id", "pipeline", "camera", "websocket",
                                   "institution_id", "user_id", "frames_processed", "jpeg_base64", "topk"])
def test_vision_detail_fields_are_rejected(client, world, cdata, as_user, db_conn, field):
    body = _mutate(world)
    body[field] = 1
    response = _post(client, as_user, cdata.vision, body)
    assert response.status_code == 400 and response.get_json()["details"] == {"unknown_fields": [field]}
    assert _auto_counts(db_conn, cdata.vision) == []


@pytest.mark.parametrize("field", ["boxes", "xyxy", "score", "family_code"])
def test_detail_fields_inside_counts_are_rejected(client, world, cdata, as_user, field):
    body = _mutate(world, counts=[{"family_id": str(world.families["a"]), "detected_quantity": 1, field: []}])
    assert _post(client, as_user, cdata.vision, body).status_code == 400


def test_malformed_bodies_are_400(client, world, cdata, as_user):
    headers = as_user("op_a", **{"Content-Type": "application/json"})
    assert client.post(_url(cdata.vision), data="{", headers=headers).status_code == 400
    assert client.post(_url(cdata.vision), data="[]", headers=headers).status_code == 400
    xml = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "x">]><vision_counts/>'
    assert client.post(_url(cdata.vision), data=xml,
                       headers=as_user("op_a", **{"Content-Type": "application/xml"})).status_code == 400


def test_body_too_large_is_413(client, world, cdata, as_user):
    body = _mutate(world, model_version="m" * (70 * 1024))
    assert _post(client, as_user, cdata.vision, body).status_code == 413


# --- Idempotency ---------------------------------------------------------------------------------


def test_identical_replay_is_200_with_same_event(client, world, cdata, as_user, db_conn):
    body = vision_body(world, ("a", 2), ("b", 1))
    first = _post(client, as_user, cdata.vision, body)
    second = _post(client, as_user, cdata.vision, body)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.get_json() == second.get_json()
    assert len(_auto_counts(db_conn, cdata.vision)) == 1


def test_replay_with_reordered_counts_and_equivalent_instant_is_200(client, world, cdata, as_user, db_conn):
    body = vision_body(world, ("a", 2), ("b", 1))
    first = _post(client, as_user, cdata.vision, body).get_json()
    reordered = dict(body, counts=list(reversed(body["counts"])), occurred_at="2026-10-14T07:00:00+02:00")
    second = _post(client, as_user, cdata.vision, reordered)
    assert second.status_code == 200 and second.get_json()["count_event_id"] == first["count_event_id"]
    assert len(_auto_counts(db_conn, cdata.vision)) == 1


@pytest.mark.parametrize("change", [
    {"counts": [{"family": "a", "q": 3}, {"family": "b", "q": 1}]},     # different quantity
    {"counts": [{"family": "a", "q": 2}]},                              # family dropped
    {"model_version": "yolo26l-v2"},
    {"trigger": "stable_window"},
    {"occurred_at": "2026-10-14T05:00:01Z"},
])
def test_same_client_event_id_with_other_content_is_409(client, world, cdata, as_user, db_conn, change):
    body = vision_body(world, ("a", 2), ("b", 1))
    assert _post(client, as_user, cdata.vision, body).status_code == 201
    changed = dict(body)
    for key, value in change.items():
        changed[key] = ([{"family_id": str(world.families[i["family"]]), "detected_quantity": i["q"]} for i in value]
                        if key == "counts" else value)
    response = _post(client, as_user, cdata.vision, changed)
    assert response.status_code == 409
    assert response.get_json()["details"] == {"client_event_id": body["client_event_id"]}
    assert len(_auto_counts(db_conn, cdata.vision)) == 1


def test_same_client_event_id_in_other_session_is_409(client, world, cdata, as_user, db_conn):
    body = vision_body(world, ("a", 1))
    assert _post(client, as_user, cdata.vision, body).status_code == 201
    db_conn.execute(text("UPDATE work_session SET capture_mode = 'vision' WHERE id = :s"), {"s": cdata.manual})
    assert _post(client, as_user, cdata.manual, body).status_code == 409
    assert _auto_counts(db_conn, cdata.manual) == [] and len(_auto_counts(db_conn, cdata.vision)) == 1


def test_client_event_id_of_another_event_type_is_409(client, world, cdata, as_user, db_conn):
    existing = uuid.uuid4()
    db_conn.execute(text("INSERT INTO count_event (event_type_id, client_event_id, session_id) VALUES "
                         "((SELECT id FROM cat_event_type WHERE code = 'session_open'), :ce, :s)"),
                    {"ce": existing, "s": cdata.vision})
    assert _post(client, as_user, cdata.vision, vision_body(world, client_event_id=existing)).status_code == 409


def test_replay_after_session_left_in_progress_is_still_200(client, world, cdata, as_user, db_conn):
    body = vision_body(world, ("a", 2))
    first = _post(client, as_user, cdata.vision, body).get_json()
    set_status(db_conn, cdata.vision, "awaiting_spd_review")
    replay = _post(client, as_user, cdata.vision, body)
    assert replay.status_code == 200 and replay.get_json() == first
    fresh = _post(client, as_user, cdata.vision, vision_body(world, ("a", 2)))
    assert fresh.status_code == 409 and len(_auto_counts(db_conn, cdata.vision)) == 1
