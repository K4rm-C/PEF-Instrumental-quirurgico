"""MS-06 Supervisor reports: outcomes, automatic count results, human interventions, activity per station and the
CSV / PDF export (real PostgreSQL rows, hand-computed results).

On top of test_reports.rep (September 2026, institution A):
- s1 (closed, station A): initial 10:05, final 10:15 at the first attempt; session_close safe → approved;
- s2 (correction_required, station A): initial 09:05, rejected final 09:20, final after a recapture 09:30;
- s5 (in_progress, station A2): initial 08:05 only;
- s3 (awaiting_spd_review, no station): contingency report;
- s7 (closed, station A2): session_close unsafe → unresolved.
Institution B's session b1 uses station B and must never appear.
"""

from __future__ import annotations

import csv
import io
import json
import uuid

import pytest
from lxml import etree
from sqlalchemy import text

from test_reports import BASE, SEPTEMBER, T, XSD, get, rep  # noqa: F401  (fixture)


@pytest.fixture
def ms06(rep, world, db_conn):
    c = db_conn
    run = lambda sql, **p: c.execute(text(sql), p)  # noqa: E731
    code = lambda table, value: c.execute(text(f"SELECT id FROM {table} WHERE code = :c"), {"c": value}).scalar_one()  # noqa: E731
    station_a2 = uuid.uuid4()
    run("INSERT INTO capture_station (id, name, room_id) VALUES (:id, :n, :r)", id=station_a2,
        n=f"=HYPERLINK(evil) {world.tag}", r=world.rooms["A"])   # a name a spreadsheet would execute
    camera = uuid.uuid4()
    run("INSERT INTO capture_camera (id, institution_id, station_id, name) VALUES (:id, :i, :s, 'Overhead')",
        id=camera, i=world.institutions["A"], s=world.stations["A"])
    s = rep.sessions
    for key, station in (("s1", world.stations["A"]), ("s2", world.stations["A"]), ("s5", station_a2),
                         ("b1", world.stations["B"])):
        run("UPDATE work_session SET station_id = :st WHERE id = :s", st=station, s=s[key])
    run("UPDATE work_session SET camera_id = :c WHERE id = :s", c=camera, s=s["s1"])

    def event(sid, kind, at, payload, family=None, detected=None):
        run("INSERT INTO count_event (id, event_type_id, family_id, detected_quantity, payload, occurred_at, session_id) "
            "VALUES (:id, :t, :f, :d, CAST(:p AS jsonb), :o, :s)", id=uuid.uuid4(), t=code("cat_event_type", kind),
            f=family, d=detected, p=json.dumps(payload), o=at, s=sid)

    event(s["s1"], "auto_count", T(5, 10, 5), {"trigger": "initial"}, detected=3)
    event(s["s1"], "auto_count", T(5, 10, 15), {"trigger": "final", "recapture": False}, detected=3)
    event(s["s1"], "session_close", T(5, 11), {"closed_by": "spd_supervisor", "safe_close_confirmed": True})
    event(s["s2"], "auto_count", T(6, 9, 5), {"trigger": "initial"}, detected=2)
    event(s["s2"], "close_blocked", T(6, 9, 20), {"reason": "final_count_mismatch"}, detected=1)
    event(s["s2"], "auto_count", T(6, 9, 30), {"trigger": "final", "recapture": True}, detected=2)
    event(s["s5"], "auto_count", T(9, 8, 5), {"trigger": "initial"}, detected=1)
    event(s["s3"], "manual_close", T(7, 8, 9), {"contingency": {"reason_code": "worker_unavailable"}})
    # s7: closed with an unreconciled shortage at station A2.
    operation, s7 = uuid.uuid4(), uuid.uuid4()
    run("INSERT INTO operation (id, scheduled_at, status_id, institution_id) VALUES (:id, :s, :st, :i)",
        id=operation, s=T(10, 8), st=code("cat_operation_status", "scheduled"), i=world.institutions["A"])
    run("INSERT INTO work_session (id, started_at, ended_at, status_id, user_id, operation_id, kit_id, station_id, "
        "capture_mode) VALUES (:id, :st, :en, :s, :u, :o, :k, :stn, 'vision')", id=s7, st=T(10, 8), en=T(10, 8, 40),
        s=code("cat_session_status", "closed"), u=world.users["op_a"].id, o=operation, k=rep.kits["k1"], stn=station_a2)
    event(s7, "session_close", T(10, 9), {"closed_by": "spd_supervisor", "safe_close_confirmed": False,
                                           "unreconciled_physical_shortages": [str(uuid.uuid4())]})
    rep.sessions["s7"], rep.station_a2 = s7, station_a2
    return rep


def test_outcomes_automatic_counts_and_interventions(client, ms06, as_user):
    body = get(client, "/summary", as_user("spd_a"), **SEPTEMBER).get_json()
    # MS-09: approved_contingency is a separate outcome (none of these sessions closed after a contingency count).
    assert body["outcomes"] == {"approved": 1, "approved_contingency": 0, "unresolved": 1, "closed_without_record": 0,
                                "closed_by_operator": 0,
                                "pending_review": 2, "aborted": 0}
    auto = body["automatic_counts"]
    assert (auto["with_initial_count"], auto["with_final_count"], auto["final_first_attempt"]) == (3, 2, 1)
    assert (auto["sessions_with_rejected_final"], auto["rejected_final_attempts"], auto["recaptured_final"]) == (1, 1, 1)
    assert auto["contingency_reports"] == 1 and auto["final_first_attempt_pct"] == 50.0
    assert auto["avg_initial_to_final_minutes"] == 17.5      # (10 + 25) / 2
    assert auto["avg_start_to_initial_minutes"] == 5.0       # 5, 5, 5
    assert body["interventions"] == {"human_corrections": 1, "contingency_reports": 1, "discrepancies_resolved": 1,
                                     "rejected_final_attempts": 1}


def test_activity_per_station(client, ms06, world, as_user):
    body = get(client, "/stations", as_user("spd_a"), **SEPTEMBER).get_json()
    rows = {r["station_id"]: r for r in body["stations"]}
    a = rows[str(world.stations["A"])]
    assert (a["sessions"], a["reported"], a["closed"], a["sessions_with_discrepancies"]) == (2, 2, 1, 2)
    assert (a["with_final_count"], a["rejected_final_attempts"], a["cameras_used"], a["avg_duration_minutes"]) == (2, 1, 1, 40.0)
    a2 = rows[str(ms06.station_a2)]
    assert (a2["sessions"], a2["closed"]) == (2, 1)
    assert rows[None]["sessions"] == 2                        # s3, s4 have no station
    assert str(world.stations["B"]) not in rows               # never another institution


def test_station_filter_applies_to_every_report(client, ms06, world, as_user):
    params = {**SEPTEMBER, "station_id": str(world.stations["A"])}
    summary = get(client, "/summary", as_user("spd_a"), **params).get_json()
    assert summary["sessions"]["total"] == 2 and summary["filters"]["station_id"] == str(world.stations["A"])
    foreign = get(client, "/summary", as_user("spd_a"), **{**SEPTEMBER, "station_id": str(world.stations["B"])})
    assert foreign.status_code == 400 and foreign.get_json()["details"]["field"] == "station_id"


def test_empty_period(client, ms06, as_user):
    params = {"from": "2025-01-01", "to": "2025-01-31", "tz": "UTC"}
    body = get(client, "/summary", as_user("spd_a"), **params).get_json()
    assert body["outcomes"]["approved"] == 0 and body["automatic_counts"]["avg_initial_to_final_minutes"] is None
    assert body["automatic_counts"]["final_first_attempt_pct"] is None
    assert get(client, "/stations", as_user("spd_a"), **params).get_json()["stations"] == []


def test_roles_and_xml(client, ms06, as_user):
    assert get(client, "/stations", as_user("admin_a"), **SEPTEMBER).status_code == 403
    assert get(client, "/stations", as_user("op_a"), **SEPTEMBER).status_code == 403
    for path, root in (("/summary", "report_summary"), ("/stations", "station_report")):
        response = get(client, path, as_user("spd_a", Accept="application/xml"), **SEPTEMBER)
        document = etree.fromstring(response.data)
        assert document.tag == root and XSD.validate(document), XSD.error_log.last_error


# --- export -------------------------------------------------------------------------------------------------


def _export(client, as_user, user="spd_a", **params):
    return client.get(f"{BASE}/export", query_string={**SEPTEMBER, **params}, headers=as_user(user))


def _audits(db_conn, action):
    return db_conn.execute(text("SELECT count(*) FROM access_audit WHERE action = :a"), {"a": action}).scalar_one()


def test_csv_export_matches_the_report_and_is_audited(client, ms06, as_user, db_conn, world):
    before = _audits(db_conn, "report.export.csv")
    response = _export(client, as_user, format="csv")
    assert response.status_code == 200 and response.mimetype == "text/csv"
    assert response.headers["Content-Disposition"] == 'attachment; filename="pef-report-2026-09-01-2026-10-01.csv"'
    assert response.headers["Cache-Control"] == "no-store"
    raw = response.data.decode("utf-8")
    assert raw.startswith("﻿PEF - Supervisor report")
    rows = list(csv.reader(io.StringIO(raw.lstrip("﻿"))))
    metrics = {(r[0], r[1]): r[2] for r in rows if len(r) == 3}
    assert metrics[("Sessions", "Total")] == "6" and metrics[("Outcomes", "Approved (safe close)")] == "1"
    assert metrics[("Automatic count (YOLO)", "Rejected final count attempts")] == "1"
    assert metrics[("Human interventions", "Human corrections")] == "1"
    assert any(r and r[0] == "Activity by station" for r in rows)
    # Formula injection: the station name starting with "=" is neutralised.
    assert any(cell.startswith("'=HYPERLINK") for row in rows for cell in row)
    assert not any(cell.startswith("=") for row in rows for cell in row)
    assert "AI-human agreement compares" in raw and str(world.stations["B"]) not in raw
    assert _audits(db_conn, "report.export.csv") == before + 1


def test_export_uses_the_same_filters(client, ms06, as_user, world):
    rows = list(csv.reader(io.StringIO(_export(client, as_user, format="csv", station_id=str(world.stations["A"]))
                                       .data.decode("utf-8").lstrip("﻿"))))
    metrics = {(r[0], r[1]): r[2] for r in rows if len(r) == 3}
    assert metrics[("Sessions", "Total")] == "2"
    assert any(r[:1] == ["Filters"] and f"station_id={world.stations['A']}" in r[1] for r in rows)


def test_empty_export_says_no_data(client, ms06, as_user):
    response = _export(client, as_user, format="csv", **{"from": "2025-01-01", "to": "2025-01-31"})
    assert response.status_code == 200 and response.data.decode("utf-8").count("No data") >= 4


def test_spanish_and_pdf_exports(client, ms06, as_user, db_conn):
    spanish = _export(client, as_user, format="csv", lang="es-MX").data.decode("utf-8")
    assert "Reporte del Supervisor" in spanish and "Aprobadas (cierre seguro)" in spanish
    before = _audits(db_conn, "report.export.pdf")
    pdf = _export(client, as_user, format="pdf", lang="es-MX")
    assert pdf.status_code == 200 and pdf.mimetype == "application/pdf" and pdf.data.startswith(b"%PDF-")
    assert len(pdf.data) > 1500 and _audits(db_conn, "report.export.pdf") == before + 1


@pytest.mark.parametrize("params, field", [({"format": "xlsx"}, "format"), ({"lang": "fr"}, "lang")])
def test_export_parameters_are_validated(client, ms06, as_user, params, field):
    response = _export(client, as_user, **params)
    assert response.status_code == 400 and response.get_json()["details"]["field"] == field
    assert _export(client, as_user, format="csv", extra="1").status_code == 400


def test_export_permissions(client, ms06, as_user, db_conn):
    before = _audits(db_conn, "report.export.csv")
    for user in ("op_a", "admin_a"):
        assert _export(client, as_user, user=user, format="csv").status_code == 403
    assert client.get(f"{BASE}/export", query_string={"format": "csv"}).status_code == 401
    assert _audits(db_conn, "report.export.csv") == before
