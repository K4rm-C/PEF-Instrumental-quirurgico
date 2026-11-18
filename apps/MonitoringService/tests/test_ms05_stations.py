"""MS-05: station heartbeat, camera state, technical alerts and the Supervisor's view (real PostgreSQL + Redis).

Institution A: room R1 with station S1 (cameras C1 active, C2 inactive) and station S2; room R-off (inactive)
with station S-off. Institution B: station SB with camera CB. The operator, supervisor and administrator
of A come from conftest.world.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from lxml import etree
from sqlalchemy import text

from conftest import make_settings
from main import create_app
from services.collector import Collector
from services.stations import StationService

BASE = "/api/v1/monitoring"
XSD = etree.XMLSchema(etree.parse(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "schemas" / "monitoring.xsd")))


def _run(conn, sql, **params):
    return conn.execute(text(sql), params)


@pytest.fixture
def plant(db_conn, world):
    c = db_conn
    p = SimpleNamespace()
    p.inst_b = uuid.uuid4()
    _run(c, "INSERT INTO institution (id, name) VALUES (:id, 'Monitoring B')", id=p.inst_b)

    def room(inst, code, active=True):
        rid = uuid.uuid4()
        _run(c, "INSERT INTO operating_room (id, code, name, active, institution_id) VALUES (:id, :c, :n, :a, :i)",
             id=rid, c=code + uuid.uuid4().hex[:4], n=f"Room {code}", a=active, i=inst)
        return rid

    def station(room_id, name, active=True):
        sid = uuid.uuid4()
        _run(c, "INSERT INTO capture_station (id, name, active, room_id) VALUES (:id, :n, :a, :r)",
             id=sid, n=name, a=active, r=room_id)
        return sid

    def camera(inst, station_id, name, active=True):
        cid = uuid.uuid4()
        _run(c, "INSERT INTO capture_camera (id, institution_id, station_id, name, device_type, resolution_width, "
                "resolution_height, fps, active) VALUES (:id, :i, :s, :n, 'usb', 1280, 720, 30, :a)",
             id=cid, i=inst, s=station_id, n=name, a=active)
        return cid

    p.r1, p.r_off, p.rb = room(world.inst, "R1"), room(world.inst, "ROFF", active=False), room(p.inst_b, "RB")
    p.s1, p.s2, p.s_off = station(p.r1, "S1"), station(p.r1, "S2"), station(p.r_off, "S-off")
    p.s_disabled = station(p.r1, "S-disabled", active=False)
    p.sb = station(p.rb, "SB")
    p.c1, p.c2 = camera(world.inst, p.s1, "C1"), camera(world.inst, p.s1, "C2", active=False)
    p.cb = camera(p.inst_b, p.sb, "CB")
    return p


def _beat(http, as_user, station, user="op", **body):
    return http.post(f"{BASE}/stations/{station}/heartbeat", json=body, headers=as_user(user))


def _status(db_conn, station):
    return _run(db_conn, "SELECT * FROM station_status WHERE station_id = :s", s=station).mappings().first()


def _events(db_conn, station):
    return _run(db_conn, "SELECT kind, resolved_at FROM station_event WHERE station_id = :s ORDER BY occurred_at, kind",
                s=station).mappings().all()


def _station(client, as_user, station, user="spd"):
    return client.get(f"{BASE}/stations/{station}", headers=as_user(user)).get_json()


def _camera(view, camera):
    return next(c for c in view["cameras"] if c["id"] == str(camera))


# --- heartbeat ------------------------------------------------------------------------------------------


def test_valid_heartbeat_marks_the_station_online_but_does_not_vouch_for_the_camera(client, as_user, plant, db_conn):
    response = _beat(client, as_user, plant.s1, camera_id=str(plant.c1), camera_status="ok", client="capture-page")
    assert response.status_code == 200
    view = response.get_json()
    assert view["status"] == "online" and view["client"] == "capture-page"
    # The station says the camera works, but VisionWorker has not seen its frames: not "ok".
    assert _camera(view, plant.c1)["status"] == "unverified" and _camera(view, plant.c1)["verified_by_server"] is False
    assert _camera(view, plant.c2)["status"] == "disabled"
    row = _status(db_conn, plant.s1)
    assert row["camera_reported"] == "ok" and row["last_reported_by"] is not None


@pytest.mark.parametrize("user, expected", [("spd", 403), ("admin", 403)])
def test_only_station_operators_send_heartbeats(client, as_user, plant, db_conn, user, expected):
    assert _beat(client, as_user, plant.s1, user=user, camera_status="ok").status_code == expected
    assert _status(db_conn, plant.s1) is None


def test_heartbeat_needs_a_valid_token(client, tokens, world, plant, db_conn):
    url = f"{BASE}/stations/{plant.s1}/heartbeat"
    assert client.post(url, json={}).status_code == 401
    op = world.users["op"]
    revoked = tokens.issue(op.id, op.institution_id, revoked=True)
    assert client.post(url, json={}, headers={"Authorization": f"Bearer {revoked}"}).status_code == 401
    forged = tokens.issue(op.id, op.institution_id, secret="another-secret-0123456789abcdef0123456789")
    assert client.post(url, json={}, headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    assert _status(db_conn, plant.s1) is None


def test_another_institution_station_or_camera_cannot_be_reported(client, as_user, plant, db_conn):
    assert _beat(client, as_user, plant.sb, camera_status="ok").status_code == 404
    assert _status(db_conn, plant.sb) is None
    response = _beat(client, as_user, plant.s1, camera_id=str(plant.cb), camera_status="ok")
    assert response.status_code == 422 and _status(db_conn, plant.s1) is None
    assert _beat(client, as_user, plant.s1, camera_id=str(uuid.uuid4())).status_code == 422


def test_disabled_station_room_or_camera_is_refused(client, as_user, plant, db_conn):
    assert _beat(client, as_user, plant.s_disabled).get_json()["details"]["reason"] == "station_disabled"
    assert _beat(client, as_user, plant.s_off).status_code == 409
    response = _beat(client, as_user, plant.s1, camera_id=str(plant.c2), camera_status="ok")
    assert response.status_code == 409 and response.get_json()["details"]["reason"] == "camera_disabled"
    assert _status(db_conn, plant.s_disabled) is None and _status(db_conn, plant.s1) is None


@pytest.mark.parametrize("body, field", [
    ({"camera_status": "great"}, "camera_status"), ({"camera_error": "has spaces and !"}, "camera_error"),
    ({"client": "x" * 41}, "client"), ({"camera_id": "not-a-uuid"}, "camera_id"),
])
def test_invalid_heartbeat_bodies(client, as_user, plant, body, field):
    response = _beat(client, as_user, plant.s1, **body)
    assert response.status_code == 400 and response.get_json()["details"]["field"] == field


def test_unknown_fields_and_non_json_are_refused(client, as_user, plant):
    assert _beat(client, as_user, plant.s1, healthy=True).status_code == 400
    response = client.post(f"{BASE}/stations/{plant.s1}/heartbeat", data="camera_status=ok", headers=as_user("op"))
    assert response.status_code == 415


# --- camera errors, loss of communication and recovery ---------------------------------------------------


def test_camera_error_opens_one_alert_and_recovery_closes_it(client, as_user, plant, db_conn):
    for _ in range(3):   # repeated error reports never duplicate the alert
        view = _beat(client, as_user, plant.s1, camera_id=str(plant.c1), camera_status="error",
                     camera_error="NotReadableError").get_json()
    assert view["status"] == "online" and _camera(view, plant.c1)["status"] == "error"
    assert _camera(view, plant.c1)["error"] == "NotReadableError" and view["open_alerts"] == 1
    assert [(e["kind"], e["resolved_at"] is None) for e in _events(db_conn, plant.s1)] == [("camera_error", True)]
    view = _beat(client, as_user, plant.s1, camera_id=str(plant.c1), camera_status="ok").get_json()
    assert view["events_recorded"] == ["camera_recovered"] and view["open_alerts"] == 0
    kinds = [(e["kind"], e["resolved_at"] is not None) for e in _events(db_conn, plant.s1)]
    assert kinds == [("camera_error", True), ("camera_recovered", False)]


def test_unavailable_camera_is_an_error_too(client, as_user, plant):
    view = _beat(client, as_user, plant.s1, camera_id=str(plant.c1), camera_status="unavailable").get_json()
    assert _camera(view, plant.c1)["status"] == "error" and view["events_recorded"] == ["camera_error"]


def test_station_that_stops_reporting_goes_offline_and_recovers(client, as_user, plant, db_conn, database):
    _beat(client, as_user, plant.s1, camera_id=str(plant.c1), camera_status="ok")
    _run(db_conn, "UPDATE station_status SET last_seen_at = now() - interval '10 minutes' WHERE station_id = :s", s=plant.s1)
    view = _station(client, as_user, plant.s1)
    assert view["status"] == "offline" and view["seconds_since_seen"] >= 600
    assert _camera(view, plant.c1)["status"] == "unknown"   # an offline station says nothing about its camera
    service = StationService(database, 90, 300)
    # The shared test database may hold other stale stations (e.g. left by the E2E runs): count this station's alerts.
    assert service.detect_lost() >= 1 and service.detect_lost() == 0     # one open alert, never twice
    assert _run(db_conn, "SELECT count(*) FROM station_event WHERE station_id = :s AND kind = 'heartbeat_lost' "
                         "AND resolved_at IS NULL", s=plant.s1).scalar_one() == 1
    assert _station(client, as_user, plant.s1)["open_alerts"] == 1
    view = _beat(client, as_user, plant.s1, camera_id=str(plant.c1), camera_status="ok").get_json()
    assert view["status"] == "online" and view["events_recorded"] == ["heartbeat_recovered"] and view["open_alerts"] == 0


def test_never_seen_and_disabled_states(client, as_user, plant):
    items = {i["name"]: i for i in client.get(f"{BASE}/stations", headers=as_user("spd")).get_json()["items"]}
    assert items["S2"]["status"] == "never_seen" and items["S-disabled"]["status"] == "disabled"
    assert items["S-off"]["status"] == "disabled"   # its room is inactive


# --- server-side evidence (VisionWorker frames) ------------------------------------------------------


def _session_at(db_conn, world, station, camera):
    status = _run(db_conn, "SELECT id FROM cat_session_status WHERE code = 'in_progress'").scalar_one()
    op_status = _run(db_conn, "SELECT id FROM cat_operation_status ORDER BY code LIMIT 1").scalar_one()
    operation, session = uuid.uuid4(), uuid.uuid4()
    _run(db_conn, "INSERT INTO operation (id, status_id, institution_id) VALUES (:id, :s, :i)",
         id=operation, s=op_status, i=world.inst)
    _run(db_conn, "INSERT INTO work_session (id, status_id, user_id, operation_id, station_id, camera_id) "
                  "VALUES (:id, :s, :u, :o, :st, :c)", id=session, s=status, u=world.users["op"].id, o=operation,
         st=station, c=camera)
    return session


def test_frames_seen_by_visionworker_make_the_camera_ok(client, as_user, plant, db_conn, world, database):
    _beat(client, as_user, plant.s1, camera_id=str(plant.c1), camera_status="ok")
    session = _session_at(db_conn, world, plant.s1, plant.c1)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    service = StationService(database, 90, 300)
    assert service.observe_frames([{"session_id": str(session), "last_frame_at": now, "frames": 12},
                                   {"session_id": str(uuid.uuid4()), "last_frame_at": now},   # unknown session
                                   {"session_id": "garbage"}]) == 1
    camera = _camera(_station(client, as_user, plant.s1), plant.c1)
    assert camera["status"] == "ok" and camera["verified_by_server"] is True and camera["frames_seen_at"]
    # Old frames are no evidence any more.
    _run(db_conn, "UPDATE station_status SET frames_seen_at = now() - interval '1 hour' WHERE station_id = :s", s=plant.s1)
    assert _camera(_station(client, as_user, plant.s1), plant.c1)["status"] == "unverified"


def test_a_reported_error_wins_over_old_frames(client, as_user, plant, db_conn, world, database):
    session = _session_at(db_conn, world, plant.s1, plant.c1)
    StationService(database, 90, 300).observe_frames([{"session_id": str(session),
                                                       "last_frame_at": datetime.now(timezone.utc).isoformat()}])
    view = _beat(client, as_user, plant.s1, camera_id=str(plant.c1), camera_status="error", camera_error="Disconnected").get_json()
    assert _camera(view, plant.c1)["status"] == "error"


def test_collector_cycle_reads_visionworker_activity(database, plant, db_conn, world, http_server, redis_client):
    session = _session_at(db_conn, world, plant.s1, plant.c1)
    base = http_server(200, {"ok": True, "sessions": [{"session_id": str(session), "frames": 3,
                                                       "last_frame_at": datetime.now(timezone.utc).isoformat()}]})
    collector = Collector(make_settings(targets={"vision": base}), database, redis_client)
    try:
        stats = collector.stations_cycle()
    finally:
        collector.close()
    assert stats["frames"] == 1 and _status(db_conn, plant.s1)["frames_camera_id"] == plant.c1


def test_collector_survives_visionworker_down(database, closed_port, redis_client):
    collector = Collector(make_settings(targets={"vision": f"http://127.0.0.1:{closed_port}"}), database, redis_client)
    try:
        assert collector.stations_cycle()["frames"] == 0
    finally:
        collector.close()


# --- Supervisor / Administrator queries ------------------------------------------------------------------


def test_supervisor_sees_only_own_institution(client, as_user, plant):
    body = client.get(f"{BASE}/stations", headers=as_user("spd")).get_json()
    names = {i["name"] for i in body["items"]}
    assert {"S1", "S2", "S-off", "S-disabled"} <= names and "SB" not in names
    assert client.get(f"{BASE}/stations/{plant.sb}", headers=as_user("spd")).status_code == 404
    assert client.get(f"{BASE}/stations", headers=as_user("admin")).status_code == 200
    assert client.get(f"{BASE}/stations", headers=as_user("op")).status_code == 403
    assert client.get(f"{BASE}/stations?x=1", headers=as_user("spd")).status_code == 400


def test_alerts_endpoint(client, as_user, plant):
    _beat(client, as_user, plant.s1, camera_id=str(plant.c1), camera_status="error", camera_error="E1")
    open_alerts = client.get(f"{BASE}/alerts", headers=as_user("spd")).get_json()["items"]
    assert [(a["kind"], a["station_name"], a["open"]) for a in open_alerts] == [("camera_error", "S1", True)]
    _beat(client, as_user, plant.s1, camera_id=str(plant.c1), camera_status="ok")
    assert client.get(f"{BASE}/alerts", headers=as_user("spd")).get_json()["items"] == []
    every = client.get(f"{BASE}/alerts?status=all", headers=as_user("admin")).get_json()["items"]
    assert {a["kind"] for a in every} == {"camera_error", "camera_recovered"}
    assert client.get(f"{BASE}/alerts?status=bad", headers=as_user("spd")).status_code == 400
    assert client.get(f"{BASE}/alerts", headers=as_user("op")).status_code == 403


def test_xml_responses_follow_the_xsd(client, as_user, plant):
    _beat(client, as_user, plant.s1, camera_id=str(plant.c1), camera_status="error", camera_error="E1")
    for path in (f"{BASE}/stations", f"{BASE}/stations/{plant.s1}", f"{BASE}/alerts?status=all"):
        response = client.get(path, headers=as_user("spd", Accept="application/xml"))
        assert response.status_code == 200 and "xml" in response.content_type
        document = etree.fromstring(response.data)
        assert XSD.validate(document), (path, XSD.error_log.last_error)


def test_state_survives_a_restart(client, as_user, plant, database, redis_client):
    _beat(client, as_user, plant.s1, camera_id=str(plant.c1), camera_status="error", camera_error="E1")
    restarted = create_app(make_settings(), database=database, redis_client=redis_client).test_client()
    view = restarted.get(f"{BASE}/stations/{plant.s1}", headers=as_user("spd")).get_json()
    assert view["status"] == "online" and _camera(view, plant.c1)["status"] == "error" and view["open_alerts"] == 1


def test_camera_of_another_institution_is_refused_by_the_database(db_conn, plant, world):
    nested = db_conn.begin_nested()
    with pytest.raises(Exception, match="does not own station"):
        _run(db_conn, "INSERT INTO capture_camera (institution_id, station_id, name) VALUES (:i, :s, 'X')",
             i=world.inst, s=plant.sb)
    nested.rollback()
