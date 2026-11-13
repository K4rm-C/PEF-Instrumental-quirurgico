"""MS-07 change feed on COMMITTED data: first sync, incremental, paging, cursors, concurrency, isolation."""

from __future__ import annotations

import base64
import json
import time
import uuid

import pytest
from sqlalchemy import text

from services.sync_feed import CursorCodec

URL = "/api/v1/sync/changes"


@pytest.fixture
def feed(committed_app, committed, tokens):
    client = committed_app.test_client()

    def get(user="spd_a", **params):
        u = committed.users[user]
        return client.get(URL, query_string=params, headers={"Authorization": f"Bearer {tokens.user(u.id, u.institution_id)}"})

    def drain(user="spd_a", cursor=None, types="operating_room,physician", limit=50):
        seen, pages = [], 0
        while True:
            params = {"types": types, "limit": limit, **({"cursor": cursor} if cursor else {})}
            body = get(user, **params).get_json()
            seen += body["changes"]
            cursor, pages = body["next_cursor"], pages + 1
            if not body["has_more"]:
                return seen, cursor, pages
    get.drain = drain
    get.client = client
    return get


def ours(changes, committed):
    ids = {str(committed.rooms["A"]), str(committed.rooms["B"]), str(committed.physicians["A"]), str(committed.physicians["B"])}
    return [c for c in changes if c["resource_id"] in ids or c["resource_id"] in committed.extra]


@pytest.fixture(autouse=True)
def _extra(committed):
    committed.extra = set()


def add_room(committed, code, *, inst="A"):
    room = uuid.uuid4()
    with committed.engine.begin() as c:
        c.execute(text("INSERT INTO operating_room (id, code, name, institution_id) VALUES (:id, :c, :n, :i)"),
                  {"id": room, "c": f"{code}-{committed.tag}", "n": code, "i": committed.inst[inst]})
    committed.extra.add(str(room))
    return room


def test_first_sync_returns_the_state_of_the_institution_only(feed, committed):
    changes, cursor, _ = feed.drain()
    mine = ours(changes, committed)
    assert {(c["resource_type"], c["resource_id"]) for c in mine} == {
        ("operating_room", str(committed.rooms["A"])), ("physician", str(committed.physicians["A"]))}
    room = next(c for c in mine if c["resource_type"] == "operating_room")
    assert room["operation"] == "create" and room["data"]["code"] == f"OR-A-{committed.tag}" and room["version"] == 1
    assert all(c["resource_id"] not in (str(committed.rooms["B"]), str(committed.physicians["B"])) for c in changes)
    again = feed(types="operating_room,physician", cursor=cursor).get_json()
    assert ours(again["changes"], committed) == []                     # nothing new: no duplicates


def test_incremental_update_deactivation_and_delete(feed, committed):
    _, cursor, _ = feed.drain()
    with committed.engine.begin() as c:
        c.execute(text("UPDATE operating_room SET name = 'Renamed' WHERE id = :id"), {"id": committed.rooms["A"]})
        c.execute(text("UPDATE operating_room SET name = 'Renamed' WHERE id = :id"), {"id": committed.rooms["A"]})  # no-op
        c.execute(text("UPDATE physician SET active = FALSE WHERE id = :id"), {"id": committed.physicians["A"]})
    extra = add_room(committed, "TMP")
    with committed.engine.begin() as c:
        c.execute(text("DELETE FROM operating_room WHERE id = :id"), {"id": extra})
    changes, _, _ = feed.drain(cursor=cursor)
    mine = [(c["resource_type"], c["operation"], c["version"]) for c in ours(changes, committed)]
    assert mine == [("operating_room", "update", 2), ("physician", "deactivate", 2),
                    ("operating_room", "create", 1), ("operating_room", "delete", 2)]
    deleted = [c for c in ours(changes, committed) if c["operation"] == "delete"][0]
    assert deleted["data"] is None and deleted["resource_id"] == str(extra)


def test_paging_is_stable_and_resumable(feed, committed):
    _, cursor, _ = feed.drain()
    rooms = [add_room(committed, f"P{i}") for i in range(7)]
    first = feed(types="operating_room", limit=3, cursor=cursor).get_json()
    assert first["has_more"] is True and len(first["changes"]) == 3
    # interrupted here; the client resumes from the last cursor it stored
    rest, _, pages = feed.drain(cursor=first["next_cursor"], types="operating_room", limit=3)
    got = [c["resource_id"] for c in ours(first["changes"] + rest, committed)]
    assert got == [str(r) for r in rooms] and pages >= 2                # same order as committed, none twice


def test_a_long_transaction_is_never_skipped(feed, committed):
    """T1 starts first and commits LAST; T2 commits in between. A client paging while T1 is open must still
    receive T1's change afterwards (txid watermark), and exactly once."""
    _, cursor, _ = feed.drain()
    slow = committed.engine.connect()
    t1 = slow.begin()
    late_room = uuid.uuid4()
    slow.execute(text("INSERT INTO operating_room (id, code, name, institution_id) VALUES (:id, :c, 'Late', :i)"),
                 {"id": late_room, "c": f"LATE-{committed.tag}", "i": committed.inst["A"]})
    committed.extra.add(str(late_room))
    quick = add_room(committed, "QUICK")
    during, cursor_during, _ = feed.drain(cursor=cursor, types="operating_room")
    assert ours(during, committed) == []          # QUICK is held back: an older transaction is still open
    t1.commit()
    slow.close()
    after, _, _ = feed.drain(cursor=cursor_during, types="operating_room")
    assert [c["resource_id"] for c in ours(after, committed)] == [str(late_room), str(quick)]


def test_cursor_tampering_expiry_and_parameters(feed, committed, settings):
    body = feed(types="operating_room").get_json()
    version, payload, signature = body["next_cursor"].split(".")
    forged_payload = base64.urlsafe_b64encode(json.dumps({"t": "0", "s": 0, "i": int(time.time())}).encode()).decode().rstrip("=")
    forged = feed(types="operating_room", cursor=f"{version}.{forged_payload}.{signature}")
    assert forged.status_code == 400 and forged.get_json()["details"]["reason"] == "invalid_cursor"
    assert feed(cursor="garbage").status_code == 400
    old = CursorCodec(settings.cursor_key, 30)
    stale_payload = base64.urlsafe_b64encode(json.dumps({"t": "1", "s": 1, "i": int(time.time()) - 31 * 86400},
                                                        separators=(",", ":")).encode()).decode().rstrip("=")
    expired = feed(cursor=f"v1.{stale_payload}.{old._sign(stale_payload)}")
    assert expired.status_code == 410 and expired.get_json()["error"] == "cursor_expired"
    assert feed(limit=0).status_code == 400 and feed(limit=100000).status_code == 400
    assert feed(types="patient").status_code == 400 and feed(unknown="1").status_code == 400


def test_visibility_by_role(feed, committed):
    assert feed("op_a", types="physician").status_code == 403          # operators do not sync physicians
    assert feed("op_a", types="operating_room,kit").status_code == 200
    resources = feed.client.get("/api/v1/sync/resources", headers={"Authorization": "Bearer x"})
    assert resources.status_code == 401


def test_operator_sees_only_its_own_sessions(feed, committed):
    with committed.engine.begin() as c:
        station = uuid.uuid4()
        c.execute(text("INSERT INTO capture_station (id, name, room_id) VALUES (:id, 'S', :r)"),
                  {"id": station, "r": committed.rooms["A"]})
        sessions = {}
        for owner in ("op_a", "spd_a"):
            sid = uuid.uuid4()
            c.execute(text("INSERT INTO work_session (id, status_id, user_id, operation_id, station_id) VALUES "
                           "(:id, (SELECT id FROM cat_session_status WHERE code = 'scheduled'), :u, :o, :s)"),
                      {"id": sid, "u": committed.users[owner].id, "o": committed.operations["A"], "s": station})
            sessions[owner] = str(sid)
            committed.extra.add(str(sid))
    try:
        mine, _, _ = feed.drain("op_a", types="work_session")
        everyone, _, _ = feed.drain("spd_a", types="work_session")
        assert {c["resource_id"] for c in ours(mine, committed)} == {sessions["op_a"]}
        assert {c["resource_id"] for c in ours(everyone, committed)} == set(sessions.values())
        data = next(c for c in ours(mine, committed))["data"]
        assert data["status"] == "scheduled" and "patient" not in json.dumps(data)
    finally:
        with committed.engine.begin() as c:
            c.execute(text("DELETE FROM work_session WHERE id = ANY(:ids)"), {"ids": [uuid.UUID(s) for s in sessions.values()]})
            c.execute(text("DELETE FROM capture_station WHERE id = :id"), {"id": station})
