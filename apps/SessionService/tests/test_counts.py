"""GET /api/v1/sessions/{id}/counts — stable contract, no raw payload, legacy read compatibility."""

from __future__ import annotations

import xml.etree.ElementTree as ET

BASE = "/api/v1/sessions"
COUNT_FIELDS = ["id", "event_type_code", "occurred_at", "user_id", "client_event_id", "family_id", "family_code",
                "expected_quantity", "detected_quantity", "reason_code", "model_version", "trigger", "counts",
                "human_correction"]
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
FORBIDDEN_TOKENS = ("boxes", "xyxy", "topk", "video_t", "frames_processed", "frame_index", "run-123", "run_id",
                    "pipeline", "best_confidence", "assignment_metrics", "payload", "jpeg", "box_count")


def _counts(client, world, as_user, user="op_a", **headers):
    return client.get(f"{BASE}/{world.sessions['a3'].id}/counts", headers=as_user(user, **headers))


def _by_id(world, items):
    return {item["id"]: item for item in items}


def test_counts_order_and_fields(client, world, as_user):
    response = _counts(client, world, as_user)
    assert response.status_code == 200
    items = response.get_json()
    for item in items:
        assert list(item) == COUNT_FIELDS
    e = world.events
    assert [item["id"] for item in items] == [str(e[k]) for k in (
        "open", "auto_legacy", "auto_final", "manual_modern", "manual_legacy", "manual_bad_legacy", "close")]
    assert [item["event_type_code"] for item in items] == [
        "session_open", "auto_count", "auto_count", "manual_count", "manual_count", "manual_count", "manual_close"]


def test_manual_count_modern_with_human_correction(client, world, as_user):
    item = _by_id(world, _counts(client, world, as_user).get_json())[str(world.events["manual_modern"])]
    assert item == {
        "id": str(world.events["manual_modern"]), "event_type_code": "manual_count",
        "occurred_at": "2026-10-01T12:03:00Z", "user_id": str(world.users["op_a"].id), "client_event_id": None,
        "family_id": str(world.families["a"]), "family_code": f"zz_ses_{world.tag}_a",
        "expected_quantity": 2, "detected_quantity": 1, "reason_code": "shortage",
        "model_version": None, "trigger": None, "counts": [],
        "human_correction": {"justification": "La pieza estaba bajo una gasa",
                             "user_id": str(world.users["op_a"].id), "recorded_at": "2026-10-01T12:03:30Z"},
    }


def test_manual_count_legacy_reported_quantity_fallback(client, world, as_user):
    items = _by_id(world, _counts(client, world, as_user).get_json())
    legacy = items[str(world.events["manual_legacy"])]
    assert legacy["detected_quantity"] == 1          # from payload.reported_quantity (read-only fallback)
    assert legacy["reason_code"] is None             # legacy `other` is not a catalog code
    assert legacy["human_correction"] is None
    bad = items[str(world.events["manual_bad_legacy"])]
    assert bad["detected_quantity"] is None          # "3" (string) is not a valid quantity


def test_auto_count_final_normalized(client, world, as_user):
    item = _by_id(world, _counts(client, world, as_user).get_json())[str(world.events["auto_final"])]
    assert item["client_event_id"] == str(world.client_event_id)
    assert item["user_id"] is None
    assert item["model_version"] == "yolo26l-demo" and item["trigger"] == "final"
    assert item["counts"] == sorted([
        {"family_id": str(world.families["a"]), "detected_quantity": 1},
        {"family_id": str(world.families["b"]), "detected_quantity": 1},
    ], key=lambda entry: entry["family_id"])
    assert item["expected_quantity"] == 7 and item["detected_quantity"] == 2


def test_auto_count_legacy_exposes_only_safe_fields(client, world, as_user):
    response = _counts(client, world, as_user)
    item = _by_id(world, response.get_json())[str(world.events["auto_legacy"])]
    assert item["trigger"] is None                    # legacy video reasons are not DS09 triggers
    assert item["model_version"] == "yolo26l-demo"
    assert item["counts"] == sorted([
        {"family_id": str(world.families["a"]), "detected_quantity": 2},
        {"family_id": str(world.families["b"]), "detected_quantity": 1},
    ], key=lambda entry: entry["family_id"])        # invalid family id entry dropped
    raw = response.get_data(as_text=True)
    for token in FORBIDDEN_TOKENS:
        assert token not in raw, token


def test_session_open_payload_not_exposed(client, world, as_user):
    item = _by_id(world, _counts(client, world, as_user).get_json())[str(world.events["open"])]
    assert item["reason_code"] is None and item["counts"] == [] and item["human_correction"] is None
    assert "station" not in _counts(client, world, as_user).get_data(as_text=True)


def test_counts_xml(client, world, as_user):
    response = _counts(client, world, as_user, Accept="application/xml")
    assert response.mimetype == "application/xml"
    root = ET.fromstring(response.data)
    assert root.tag == "counts" and [c.tag for c in root] == ["count"] * 7
    modern = [n for n in root if n.findtext("id") == str(world.events["manual_modern"])][0]
    assert [c.tag for c in modern] == COUNT_FIELDS
    assert modern.find("human_correction").findtext("justification") == "La pieza estaba bajo una gasa"
    legacy = [n for n in root if n.findtext("id") == str(world.events["manual_legacy"])][0]
    assert legacy.find("human_correction").get(XSI_NIL) == "true"
    assert legacy.findtext("detected_quantity") == "1"
    final = [n for n in root if n.findtext("id") == str(world.events["auto_final"])][0]
    assert [c.tag for c in final.find("counts")] == ["count", "count"]
    for token in FORBIDDEN_TOKENS:
        assert token not in response.get_data(as_text=True), token


def test_counts_tenant_and_ownership(client, world, as_user):
    path_b = f"{BASE}/{world.sessions['b1'].id}/counts"
    assert client.get(path_b, headers=as_user("spd_a")).status_code == 404
    assert client.get(path_b, headers=as_user("op_b")).status_code == 200
    assert client.get(f"{BASE}/{world.sessions['a3'].id}/counts", headers=as_user("op_a2")).status_code == 404
