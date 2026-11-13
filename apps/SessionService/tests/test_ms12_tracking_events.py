"""MS-12 (ms-tracking-instrumental): Supervisor read of the tracking log of the accepted final run.
Real PostgreSQL (rolled back): Supervisor only, institution isolation, completeness, pagination, XML contract."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from lxml import etree
from sqlalchemy import text

BASE = "/api/v1/sessions"
XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / "schemas" / "session-reports.xsd")))


def _final(db_conn, world, session_key="a2", *, recorded=3, persist=3):
    session_id = world.sessions[session_key].id
    count_id = uuid.uuid4()
    payload = {"source": "last_frame", "run_id": "run-9", "trigger": "final",
               "tracking": {"version": "ms11-hungarian-cv-1", "frames": 120, "unique_confirmed_tracks": 4,
                            "active_confirmed_tracks": 3, "final_frame_total": 3,
                            "active_tracks_match_final_frame": True, "events_recorded": recorded, "events_dropped": 0,
                            "events": [{"event": "confirmed"}], "note": "Tracking statistics are not counts"}}
    db_conn.execute(text("INSERT INTO count_event (id, event_type_id, payload, occurred_at, session_id, detected_quantity) "
                         "VALUES (:id, (SELECT id FROM cat_event_type WHERE code = 'auto_count'), CAST(:p AS jsonb), "
                         "now(), :s, 3)"), {"id": count_id, "p": json.dumps(payload), "s": session_id})
    kinds = ["confirmed", "lost", "recovered"]
    for seq in range(persist):
        db_conn.execute(text("INSERT INTO tracking_event (session_id, count_event_id, run_id, seq, video_t, frame_index, "
                             "track_id, event, family_code, confidence, detail) VALUES (:s, :c, 'run-9', :q, :t, :f, 1, "
                             ":e, 'KELLY', 0.9, CAST(:d AS jsonb))"),
                        {"s": session_id, "c": count_id, "q": seq, "t": seq * 0.5, "f": seq * 15, "e": kinds[seq % 3],
                         "d": json.dumps({"gap_s": 0.4} if kinds[seq % 3] == "recovered" else {})})
    return session_id, count_id


def test_the_supervisor_reads_the_complete_log(client, world, as_user, db_conn):
    session_id, count_id = _final(db_conn, world)
    response = client.get(f"{BASE}/{session_id}/tracking-events", headers=as_user("spd_a"))
    assert response.status_code == 200
    body = response.get_json()
    assert body["count_event_id"] == str(count_id) and body["run_id"] == "run-9"
    assert (body["events_recorded"], body["events_persisted"], body["complete"]) == (3, 3, True)
    assert [e["event"] for e in body["events"]] == ["confirmed", "lost", "recovered"]
    assert body["events"][2]["detail"] == {"gap_s": 0.4}
    assert "events" not in body["summary"] and body["summary"]["final_frame_total"] == 3


def test_an_incomplete_log_is_reported_and_pages_work(client, world, as_user, db_conn):
    session_id, _ = _final(db_conn, world, recorded=10, persist=5)
    body = client.get(f"{BASE}/{session_id}/tracking-events?offset=3&limit=1", headers=as_user("spd_a")).get_json()
    assert body["complete"] is False and body["events_persisted"] == 5 and [e["seq"] for e in body["events"]] == [3]
    assert client.get(f"{BASE}/{session_id}/tracking-events?limit=0", headers=as_user("spd_a")).status_code == 400
    assert client.get(f"{BASE}/{session_id}/tracking-events?x=1", headers=as_user("spd_a")).status_code == 400


def test_without_an_accepted_final_count_nothing_is_invented(client, world, as_user):
    body = client.get(f"{BASE}/{world.sessions['a1'].id}/tracking-events", headers=as_user("spd_a")).get_json()
    assert body["count_event_id"] is None and body["events"] == [] and body["complete"] is None


def test_only_the_supervisor_of_the_institution_reads_it(client, world, as_user, db_conn):
    session_id, _ = _final(db_conn, world)
    assert client.get(f"{BASE}/{session_id}/tracking-events", headers=as_user("op_a")).status_code == 403
    assert client.get(f"{BASE}/{session_id}/tracking-events", headers=as_user("spd_b")).status_code == 404
    assert client.get(f"{BASE}/not-a-uuid/tracking-events", headers=as_user("spd_a")).status_code == 404


def test_xml_contract(client, world, as_user, db_conn):
    session_id, _ = _final(db_conn, world)
    xml = client.get(f"{BASE}/{session_id}/tracking-events", headers=as_user("spd_a", Accept="application/xml"))
    assert XSD.validate(etree.fromstring(xml.data)), XSD.error_log
    empty = client.get(f"{BASE}/{world.sessions['a1'].id}/tracking-events",
                       headers=as_user("spd_a", Accept="application/xml"))
    assert XSD.validate(etree.fromstring(empty.data)), XSD.error_log
