"""MS-01 / P5: scheduling refuses inactive rooms, stations, kits and phases; existing sessions keep theirs.

Real PostgreSQL/Redis in a rolled-back transaction (see conftest). Procedures (cat_procedure_type) have no
active flag in the schema (001_init.sql): a procedure can only be unknown, never inactive.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from lxml import etree

from test_supervisor_flow import BASE, XML, XSD, plan, reason, schedule_body  # noqa: F401 - plan is a fixture
from write_fixtures import wdata  # noqa: F401 - fixture


@pytest.fixture
def inactive(world, db_conn):
    """Inactive resources of institution A next to the active ones of the world."""
    c, inst = db_conn, world.institutions["A"]
    r = type("Inactive", (), {})()
    r.room, r.station_in_room, r.station = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    c.execute(text("INSERT INTO operating_room (id, code, name, active, institution_id) "
                   "VALUES (:id, :c, 'Closed room', FALSE, :i)"), {"id": r.room, "c": f"ZZ-OFF-{r.room.hex[:6]}", "i": inst})
    c.execute(text("INSERT INTO capture_station (id, name, active, room_id) VALUES (:id, 'Station of closed room', TRUE, :r)"),
              {"id": r.station_in_room, "r": r.room})
    c.execute(text("INSERT INTO capture_station (id, name, active, room_id) VALUES (:id, 'Retired station', FALSE, :r)"),
              {"id": r.station, "r": world.rooms["A"]})
    r.kit = uuid.uuid4()
    c.execute(text("INSERT INTO kit (id, name, version, active, institution_id) VALUES (:id, :n, 1, FALSE, :i)"),
              {"id": r.kit, "n": f"Superseded kit {r.kit.hex[:6]}", "i": inst})
    c.execute(text("INSERT INTO kit_item (kit_id, family_id, quantity) VALUES (:k, :f, 1)"),
              {"k": r.kit, "f": world.families["a"]})
    r.phase = f"zz_off_{uuid.uuid4().hex[:6]}"
    c.execute(text("INSERT INTO cat_operation_phase (id, code, name, active) VALUES (:id, :c, 'Retired phase', FALSE)"),
              {"id": uuid.uuid4(), "c": r.phase})
    return r


def _schedule(client, world, plan, as_user, **overrides):
    return client.post(f"{BASE}/schedule", json=schedule_body(world, plan, **overrides), headers=as_user("spd_a"))


def _count_sessions(db_conn):
    return db_conn.execute(text("SELECT count(*) FROM work_session")).scalar_one()


@pytest.mark.parametrize("override, expected", [
    (lambda r, w: {"room_id": str(r.room), "station_id": str(r.station_in_room)}, "inactive_room"),
    (lambda r, w: {"station_id": str(r.station)}, "inactive_station"),
    (lambda r, w: {"kit_id": str(r.kit)}, "inactive_kit"),
    (lambda r, w: {"phase_code": r.phase}, "inactive_phase"),
])
def test_schedule_refuses_inactive_resources(client, world, plan, as_user, db_conn, inactive, override, expected):
    before = _count_sessions(db_conn)
    response = _schedule(client, world, plan, as_user, **override(inactive, world))
    assert response.status_code == 422, response.get_data(as_text=True)
    assert response.get_json()["error"] == "validation_error" and reason(response) == expected
    assert _count_sessions(db_conn) == before


@pytest.mark.parametrize("override, expected", [
    (lambda w: {"room_id": str(w.rooms["B"])}, "invalid_room"),
    (lambda w: {"room_id": str(uuid.uuid4())}, "invalid_room"),
    (lambda w: {"station_id": str(w.stations["B"])}, "invalid_station"),
    (lambda w: {"kit_id": str(w.kits["B"])}, "invalid_kit"),
    (lambda w: {"kit_id": str(uuid.uuid4())}, "invalid_kit"),
    (lambda w: {"procedure_type_id": str(uuid.uuid4())}, "invalid_procedure"),
    (lambda w: {"phase_code": "no_such_phase"}, "invalid_phase"),
])
def test_unknown_or_foreign_resources_keep_their_previous_answer(client, world, plan, as_user, override, expected):
    response = _schedule(client, world, plan, as_user, **override(world))
    assert response.status_code == 422 and reason(response) == expected


def test_xml_schedule_gets_the_same_reason(client, world, plan, as_user, inactive):
    """Same shape as test_supervisor_flow.test_schedule_xml_conforms_to_the_request_schema, inactive kit."""
    body = (f"<schedule_request><operator_user_id>{world.users['op_a'].id}</operator_user_id>"
            f"<procedure_type_id>{plan.procedure}</procedure_type_id><room_id>{world.rooms['A']}</room_id>"
            f"<station_id>{world.stations['A']}</station_id><kit_id>{inactive.kit}</kit_id>"
            "<scheduled_at>2026-11-02T09:30:00Z</scheduled_at><phase_code>start</phase_code>"
            f"<physicians><physician><physician_id>{plan.physician}</physician_id></physician></physicians>"
            f"<expected_lines><line><family_id>{world.families['a']}</family_id><expected_quantity>1</expected_quantity>"
            "</line></expected_lines></schedule_request>").encode()
    assert XSD.validate(etree.fromstring(body))
    response = client.post(f"{BASE}/schedule", data=body, headers={**as_user("spd_a"), **XML})
    assert response.status_code == 422, response.get_data(as_text=True)
    document = etree.fromstring(response.data)
    assert document.findtext("details/reason") == "inactive_kit"


def test_editing_a_scheduled_session_cannot_introduce_inactive_resources(client, world, plan, as_user, inactive):
    session_id = _schedule(client, world, plan, as_user).get_json()["id"]
    for override, expected in (({"station_id": str(inactive.station)}, "inactive_station"),
                               ({"kit_id": str(inactive.kit)}, "inactive_kit"),
                               ({"room_id": str(inactive.room), "station_id": str(inactive.station_in_room)},
                                "inactive_room")):
        body = schedule_body(world, plan, **override)
        del body["phase_code"]
        response = client.put(f"{BASE}/{session_id}", json=body, headers=as_user("spd_a"))
        assert response.status_code == 422 and reason(response) == expected, (override, response.get_json())


def test_existing_sessions_keep_resources_deactivated_later(client, world, plan, as_user, db_conn):
    """A room, station and kit deactivated after scheduling do not invalidate the session: it is still
    readable, and it can still be edited as long as those references are kept."""
    session_id = _schedule(client, world, plan, as_user).get_json()["id"]
    for table, key in (("operating_room", "rooms"), ("capture_station", "stations"), ("kit", "kits")):
        db_conn.execute(text(f"UPDATE {table} SET active = FALSE WHERE id = :i"), {"i": getattr(world, key)["A"]})
    detail = client.get(f"{BASE}/{session_id}", headers=as_user("spd_a"))
    assert detail.status_code == 200
    body = schedule_body(world, plan, scheduled_at="2026-11-03T10:00:00Z")
    del body["phase_code"]
    response = client.put(f"{BASE}/{session_id}", json=body, headers=as_user("spd_a"))
    assert response.status_code == 200, response.get_data(as_text=True)
    row = db_conn.execute(text("SELECT ws.station_id, ws.kit_id, o.room_id FROM work_session ws "
                               "JOIN operation o ON o.id = ws.operation_id WHERE ws.id = :s"), {"s": session_id}).one()
    assert (row.station_id, row.kit_id, row.room_id) == (world.stations["A"], world.kits["A"], world.rooms["A"])


def test_historical_sessions_are_untouched_by_deactivation(client, world, as_user, db_conn):
    """Sessions created by the world fixture (other states) stay listable after their resources are retired."""
    for table, key in (("operating_room", "rooms"), ("capture_station", "stations"), ("kit", "kits")):
        db_conn.execute(text(f"UPDATE {table} SET active = FALSE WHERE id = :i"), {"i": getattr(world, key)["A"]})
    listing = client.get(f"{BASE}", headers=as_user("spd_a"))
    assert listing.status_code == 200
