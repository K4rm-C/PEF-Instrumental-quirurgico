"""Supervisor reports (/api/v1/sessions/reports/*) on real PostgreSQL rows with hand-computed results.

Data of institution A in September 2026 (UTC), plus one session of A in October and one of B:

| session | scheduled  | status              | op  | kit | counting      | expected | human / AI per family      | discrepancy                  |
| s1      | 09-05 10:00| closed              | op1 | k1  | 10:00-10:30   | a2 b1    | a 2/2 (agree), b 1/0       | b shortage, resolved in 30 min|
| s2      | 09-06 09:00| correction_required | op1 | k1  | 09:00-09:50   | a2       | a 1/1 (agree)              | a shortage, open             |
| s3      | 09-07 08:00| awaiting_spd_review | op2 | k2  | 08:00-08:10   | c4       | c 4 (legacy, no AI)        | —                            |
| s4      | 09-08 08:00| scheduled           | op2 | k2  | —             | c1       | —                          | —                            |
| s5      | 09-09 08:00| in_progress         | op1 | k1  | started       | a1       | —                          | —                            |
| s6      | 10-02      | closed (out of period) ...                                                                                  |
| b1      | 09-05      | institution B ...                                                                                           |
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from lxml import etree
from sqlalchemy import text

BASE = "/api/v1/sessions/reports"
SEPTEMBER = {"from": "2026-09-01", "to": "2026-09-30", "tz": "UTC"}
XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / "schemas" / "session-reports.xsd")))


def T(day, hour, minute=0):
    return datetime(2026, 9 if day < 32 else 10, day if day < 32 else day - 31, hour, minute, tzinfo=timezone.utc)


@pytest.fixture
def rep(world, db_conn):
    c, w = db_conn, world
    run = lambda sql, **p: c.execute(text(sql), p)  # noqa: E731
    code = lambda table, value: c.execute(text(f"SELECT id FROM {table} WHERE code = :c"), {"c": value}).scalar_one()  # noqa: E731
    r = SimpleNamespace(sessions={}, kits={})
    for key, inst, version in (("k1", "A", 3), ("k2", "A", 1), ("kb", "B", 1)):
        r.kits[key] = uuid.uuid4()
        run("INSERT INTO kit (id, name, version, institution_id) VALUES (:id, :n, :v, :i)",
            id=r.kits[key], n=f"Report kit {key} {w.tag}", v=version, i=w.institutions[inst])
    op_status = code("cat_operation_status", "scheduled")
    event = {k: code("cat_event_type", k) for k in ("manual_count", "auto_count")}
    reason = {k: code("cat_discrepancy_reason", k) for k in ("shortage", "surplus")}
    fam = w.families

    def session(key, inst, scheduled, status, user, kit, started=None, ended=None, mode="vision"):
        operation_id, sid = uuid.uuid4(), uuid.uuid4()
        run("INSERT INTO operation (id, scheduled_at, status_id, institution_id) VALUES (:id, :s, :st, :i)",
            id=operation_id, s=scheduled, st=op_status, i=w.institutions[inst])
        run("INSERT INTO work_session (id, started_at, ended_at, status_id, user_id, operation_id, kit_id, capture_mode) "
            "VALUES (:id, :st, :en, :s, :u, :o, :k, :cm)", id=sid, st=started, en=ended,
            s=code("cat_session_status", status), u=w.users[user].id, o=operation_id, k=r.kits[kit], cm=mode)
        r.sessions[key] = sid
        return sid

    def expected(sid, family, qty):
        run("INSERT INTO expected_inventory (session_id, family_id, expected_quantity) VALUES (:s, :f, :q)",
            s=sid, f=fam[family], q=qty)

    def count(sid, family, human, at, ai=None, legacy=False):
        event_id = uuid.uuid4()
        payload = {"ai": ai is not None}
        if ai is not None:
            payload["ai_suggested_quantity"] = ai
        if legacy:
            payload["reported_quantity"] = str(human)
        run("INSERT INTO count_event (id, event_type_id, family_id, detected_quantity, payload, occurred_at, session_id) "
            "VALUES (:id, :t, :f, :d, CAST(:p AS jsonb), :o, :s)", id=event_id, t=event["manual_count"], f=fam[family],
            d=None if legacy else human, p=json.dumps(payload), o=at, s=sid)
        return event_id

    def discrepancy(sid, family, why, origin, resolved_at=None):
        run("INSERT INTO discrepancy (description, resolved, resolved_at, reason_id, family_id, expected_quantity, "
            "detected_quantity, session_id, origin_event_id) VALUES ('report test', :r, :ra, :reason, :f, 1, 0, :s, :o)",
            r=resolved_at is not None, ra=resolved_at, reason=reason[why], f=fam[family], s=sid, o=origin)

    s1 = session("s1", "A", T(5, 10), "closed", "op_a", "k1", T(5, 10), T(5, 10, 30))
    expected(s1, "a", 2), expected(s1, "b", 1)
    count(s1, "a", 2, T(5, 10, 20), ai=2)
    b_count = count(s1, "b", 1, T(5, 10, 25), ai=0)
    discrepancy(s1, "b", "shortage", b_count, resolved_at=T(5, 10, 55))
    s2 = session("s2", "A", T(6, 9), "correction_required", "op_a", "k1", T(6, 9), T(6, 9, 50))
    expected(s2, "a", 2)
    discrepancy(s2, "a", "shortage", count(s2, "a", 1, T(6, 9, 40), ai=1))
    s3 = session("s3", "A", T(7, 8), "awaiting_spd_review", "op_a2", "k2", T(7, 8), T(7, 8, 10), mode="manual_no_privacy")
    expected(s3, "c", 4)
    count(s3, "c", 4, T(7, 8, 5), legacy=True)
    s4 = session("s4", "A", T(8, 8), "scheduled", "op_a2", "k2")
    expected(s4, "c", 1)
    s5 = session("s5", "A", T(9, 8), "in_progress", "op_a", "k1", T(9, 8))
    expected(s5, "a", 1)
    s6 = session("s6", "A", T(33, 8), "closed", "op_a", "k1", T(33, 8), T(33, 9))
    discrepancy(s6, "a", "surplus", None)
    b1 = session("b1", "B", T(5, 10), "closed", "op_b", "kb", T(5, 10), T(5, 10, 5))
    discrepancy(b1, "a", "surplus", None)
    r.count = count
    return r


def get(client, path, headers, **params):
    return client.get(f"{BASE}{path}", query_string=params, headers=headers)


def test_summary_of_real_sessions(client, rep, as_user):
    body = get(client, "/summary", as_user("spd_a"), **SEPTEMBER).get_json()
    s = body["sessions"]
    assert (s["total"], s["scheduled"], s["in_progress"], s["pending_review"], s["closed"]) == (5, 1, 1, 2, 1)
    assert {i["code"]: i["count"] for i in s["by_status"]} == {"scheduled": 1, "in_progress": 1, "awaiting_spd_review": 1,
                                                               "correction_required": 1, "closed": 1, "aborted": 0}
    assert s["reported"] == 3 and s["timed"] == 3 and s["avg_duration_minutes"] == 30.0     # (30 + 50 + 10) / 3
    assert s["reported_with_discrepancies"] == 2 and s["with_discrepancies_pct"] == 66.7     # s1, s2 of s1, s2, s3
    d = body["discrepancies"]
    assert (d["total"], d["open"], d["resolved"], d["avg_resolution_minutes"]) == (2, 1, 1, 30.0)
    assert [(r["code"], r["total"], r["open"]) for r in d["by_reason"]] == [("shortage", 2, 1)]
    assert sorted((f["name"], f["total"]) for f in d["by_family"]) == [("Family a", 1), ("Family b", 1)]
    assert [(a["date"], a["sessions"]) for a in body["activity"]] == [
        ("2026-09-05", 1), ("2026-09-06", 1), ("2026-09-07", 1), ("2026-09-08", 1), ("2026-09-09", 1)]
    assert body["filters"]["from"] == "2026-09-01T00:00:00Z" and body["filters"]["to"] == "2026-10-01T00:00:00Z"


def test_ai_agreement_uses_the_yolo_suggestion_not_the_expected_inventory(client, rep, as_user):
    agreement = get(client, "/summary", as_user("spd_a"), **SEPTEMBER).get_json()["ai_agreement"]
    # s1 a 2/2, s1 b 1/0, s2 a 1/1 (s2 a also differs from the expected 2, which is NOT a disagreement).
    assert (agreement["pairs"], agreement["agreed"], agreement["corrections"]) == (3, 2, 1)
    assert agreement["sufficient"] is False and agreement["agreement_pct"] is None          # < 5 pairs: no %
    for family, human, ai in (("a", 1, 1), ("b", 2, 2)):
        rep.count(rep.sessions["s5"], family, human, T(9, 8, 10), ai=ai)
    agreement = get(client, "/summary", as_user("spd_a"), **SEPTEMBER).get_json()["ai_agreement"]
    assert (agreement["pairs"], agreement["agreed"], agreement["agreement_pct"]) == (5, 4, 80.0)


def test_same_pair_rule_as_the_administration_dashboard():
    root = Path(__file__).resolve().parents[3]
    admin = (root / "apps" / "BackendWebFlask" / "services" / "admin_local" / "dashboard.py").read_text(encoding="utf-8")
    ours = (Path(__file__).resolve().parents[1] / "repositories" / "report_repository.py").read_text(encoding="utf-8")

    def core(source):
        start = source.index("COALESCE(ce.detected_quantity, (ce.payload ->> 'reported_quantity')::int) AS human_qty")
        end = source.index("AND (ce.payload ->> 'ai') = 'true'", start)
        block = source[start:end].replace("JOIN s ON s.id = ce.session_id", "")
        return re.sub(r"\s+", "", block)
    assert core(ours) == core(admin)


@pytest.mark.parametrize("params, total", [
    ({"kit_id": "k2"}, 2), ({"operator_id": "op_a"}, 3), ({"status": "closed"}, 1),
    ({"from": "2026-09-06", "to": "2026-09-07"}, 2),                         # "to" date includes that day
    ({"from": "2026-09-05T10:00:00Z", "to": "2026-09-05T10:00:01Z"}, 1),
])
def test_filters(client, rep, world, as_user, params, total):
    resolved = {k: (str(rep.kits[v]) if k == "kit_id" else str(world.users[v].id) if k == "operator_id" else v)
                for k, v in params.items()}
    body = get(client, "/summary", as_user("spd_a"), **{**SEPTEMBER, **resolved}).get_json()
    assert body["sessions"]["total"] == total


def test_kit_activity(client, rep, as_user):
    body = get(client, "/kits", as_user("spd_a"), **SEPTEMBER).get_json()
    kits = {k["kit_id"]: k for k in body["kits"]}
    assert body["total"] == 2 and list(kits) == [str(rep.kits["k1"]), str(rep.kits["k2"])]
    k1, k2 = kits[str(rep.kits["k1"])], kits[str(rep.kits["k2"])]
    assert (k1["version"], k1["sessions"], k1["reported_sessions"], k1["sessions_with_discrepancies"], k1["discrepancies"]) == (3, 3, 2, 2, 2)
    assert (k1["expected_quantity"], k1["expected_quantity_counted"], k1["reported_quantity"]) == (6, 5, 4)
    assert (k2["sessions"], k2["expected_quantity"], k2["expected_quantity_counted"], k2["reported_quantity"]) == (2, 5, 4, 4)
    paged = get(client, "/kits", as_user("spd_a"), **SEPTEMBER, per_page=1, page=2).get_json()
    assert paged["total_pages"] == 2 and [k["kit_id"] for k in paged["kits"]] == [str(rep.kits["k2"])]


def test_operator_activity(client, rep, world, as_user):
    body = get(client, "/operators", as_user("spd_a"), **SEPTEMBER).get_json()
    ops = {o["operator_id"]: o for o in body["operators"]}
    op1, op2 = ops[str(world.users["op_a"].id)], ops[str(world.users["op_a2"].id)]
    assert (op1["sessions"], op1["started_sessions"], op1["counts_completed"], op1["family_counts"]) == (3, 3, 2, 3)
    assert (op1["ai_pairs"], op1["ai_agreed"], op1["human_corrections"], op1["discrepancies"]) == (3, 2, 1, 2)
    assert op1["agreement_pct"] is None
    assert (op2["sessions"], op2["started_sessions"], op2["counts_completed"], op2["ai_pairs"], op2["discrepancies"]) == (2, 1, 1, 0, 0)
    assert op2["agreement_pct"] is None and body["min_pairs"] == 5


def test_empty_period_keeps_nulls(client, rep, as_user):
    body = get(client, "/summary", as_user("spd_a"), **{"from": "2025-01-01", "to": "2025-01-31"}).get_json()
    assert body["sessions"]["total"] == 0 and body["sessions"]["avg_duration_minutes"] is None
    assert body["sessions"]["with_discrepancies_pct"] is None and body["discrepancies"]["avg_resolution_minutes"] is None
    assert body["ai_agreement"]["agreement_pct"] is None and body["activity"] == []
    assert get(client, "/kits", as_user("spd_a"), **{"from": "2025-01-01", "to": "2025-01-31"}).get_json()["kits"] == []


def test_institution_isolation(client, rep, as_user):
    body = get(client, "/summary", as_user("spd_b"), **SEPTEMBER).get_json()
    assert body["sessions"]["total"] == 1 and body["discrepancies"]["total"] == 1
    foreign = get(client, "/summary", as_user("spd_b"), **SEPTEMBER, kit_id=str(rep.kits["k1"]))
    assert foreign.status_code == 400 and foreign.get_json()["details"]["field"] == "kit_id"
    options = get(client, "/options", as_user("spd_b")).get_json()
    assert str(rep.kits["k1"]) not in {k["id"] for k in options["kits"]}


def test_only_supervisors(client, rep, as_user, db_conn, world):
    for user in ("op_a", "admin_a"):
        for path in ("/summary", "/kits", "/operators", "/options"):
            assert get(client, path, as_user(user)).status_code == 403
    denied = db_conn.execute(text("SELECT count(*) FROM access_audit WHERE actor_user_id = :u AND outcome = 'denied'"),
                             {"u": world.users["op_a"].id}).scalar_one()
    assert denied >= 4
    assert get(client, "/summary", {}).status_code == 401


@pytest.mark.parametrize("params, field", [
    ({"from": "yesterday"}, "from"), ({"from": "2026-09-10", "to": "2026-09-01"}, "to"),
    ({"from": "2024-01-01", "to": "2026-01-01"}, "from"), ({"kit_id": "x"}, "kit_id"), ({"operator_id": str(uuid.uuid4())}, "operator_id"),
    ({"status": "done"}, "status"), ({"tz": "Mars/Base"}, "tz"), ({"page": "0"}, "page"), ({"per_page": "500"}, "per_page"),
])
def test_invalid_parameters(client, rep, as_user, params, field):
    response = get(client, "/kits", as_user("spd_a"), **params)
    assert response.status_code == 400 and response.get_json()["details"]["field"] == field


def test_unknown_parameters(client, rep, as_user):
    for path in ("/summary?institution_id=x", "/options?kit=1"):
        body = client.get(f"{BASE}{path}", headers=as_user("spd_a")).get_json()
        assert body["error"] == "invalid_request" and body["details"]["unknown_parameters"]


def test_xml_conforms_to_the_xsd(client, rep, as_user):
    headers = as_user("spd_a", Accept="application/xml")
    for path, root in (("/summary", "report_summary"), ("/kits", "kit_report"), ("/operators", "operator_report"),
                       ("/options", "report_options")):
        params = {} if path == "/options" else SEPTEMBER
        document = etree.fromstring(get(client, path, headers, **params).data)
        assert document.tag == root and XSD.validate(document), (path, XSD.error_log)
    empty = etree.fromstring(get(client, "/summary", headers, **{"from": "2025-01-01", "to": "2025-01-31"}).data)
    assert XSD.validate(empty) and empty.find("sessions/avg_duration_minutes").get(
        "{http://www.w3.org/2001/XMLSchema-instance}nil") == "true"
    error = etree.fromstring(get(client, "/summary", headers, status="done").data)
    assert XSD.validate(error) and error.tag == "error_response"
    assert client.get("/api/v1/sessions/schemas/session-reports.xsd").status_code == 200


def test_reports_run_in_read_only_transactions(pg_engine):
    from database import Database
    with pytest.raises(Exception, match="read-only"):
        with Database(pg_engine).read() as connection:
            connection.execute(text("UPDATE work_session SET updated_at = updated_at WHERE false"))
