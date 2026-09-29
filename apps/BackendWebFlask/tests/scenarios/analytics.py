"""Dashboards / reports / indicators / audit logs / LOGIN-LOGOUT audit tests."""
import io
import os
import re
import shutil
import sys
import uuid
from datetime import timedelta

from _common import APP, HERE, check, fake_user, finish

STORE = os.path.join(HERE, "evidence_analytics")
shutil.rmtree(STORE, ignore_errors=True)
os.environ["CAPTURE_STORAGE_ROOT"] = STORE

from _common import setup_app  # noqa: E402

flask_app, db = setup_app("smoke_analytics.db")

from sqlalchemy import func, select  # noqa: E402

import controllers.routes as routes  # noqa: E402
from models.AccessAudit import AccessAudit  # noqa: E402
from models.CaptureStation import CaptureStation  # noqa: E402
from models.CatDiscrepancyReason import CatDiscrepancyReason  # noqa: E402
from models.CatEventType import CatEventType  # noqa: E402
from models.CatInstrumentCategory import CatInstrumentCategory  # noqa: E402
from models.CatOperationPhase import CatOperationPhase  # noqa: E402
from models.CatOperationStatus import CatOperationStatus  # noqa: E402
from models.CatProcedureType import CatProcedureType  # noqa: E402
from models.CatSessionStatus import CatSessionStatus  # noqa: E402
from models.CountEvent import CountEvent  # noqa: E402
from models.Discrepancy import Discrepancy  # noqa: E402
from models.Institution import Institution  # noqa: E402
from models.InstrumentFamily import InstrumentFamily  # noqa: E402
from models.Kit import Kit  # noqa: E402
from models.KitItem import KitItem  # noqa: E402
from models.ModelClass import ModelClass  # noqa: E402
from models.OperatingRoom import OperatingRoom  # noqa: E402
from models.Operation import Operation  # noqa: E402
from models.ProcedureKit import ProcedureKit  # noqa: E402
from models.ProcedurePhase import ProcedurePhase  # noqa: E402
from models.User import User  # noqa: E402
from models.WorkSession import WorkSession  # noqa: E402
from models.YoloModel import YoloModel  # noqa: E402
from services import analytics_service as analytics  # noqa: E402

I = {}
with flask_app.app_context():
    a, b = Institution(name="A"), Institution(name="B")
    db.session.add_all([a, b]); db.session.flush()
    db.session.add_all([CatSessionStatus(code=c, name=c.capitalize()) for c in ("open", "counting", "validating", "closed")])
    db.session.add_all([CatEventType(code=c, name=c) for c in ("auto_count", "manual_count", "validation_passed", "close_blocked",
                                                               "session_close", "correction_requested")])
    db.session.add_all([CatDiscrepancyReason(code=c, name=n) for c, n in (("shortage", "Shortage against expected inventory"), ("surplus", "Surplus against expected inventory"),
                                                                        ("unidentified", "Item present but not identified by the model"), ("low_confidence", "Model confidence below threshold"))])
    sched = CatOperationStatus(code="scheduled", name="Scheduled")
    cat = CatInstrumentCategory(code="c", name="c"); phase = CatOperationPhase(code="pre", name="Pre")
    proc = CatProcedureType(code="P", name="Appendectomy")
    db.session.add_all([sched, cat, phase, proc]); db.session.flush()
    users = {k: User(name=n, email=f"{k}@x.org", password_hash="x", institution_id=inst.id) for k, n, inst in
             (("op", "Oper A", a), ("sup", "Super A", a), ("adm", "Admin A", a), ("op_b", "Oper B", b), ("sup_b", "Super B", b))}
    mosq, kelly = InstrumentFamily(code="MOS", name="Mosquito", category_id=cat.id), InstrumentFamily(code="KEL", name="Kelly", category_id=cat.id)
    room, room_b = OperatingRoom(code="R", name="R", institution_id=a.id), OperatingRoom(code="RB", name="RB", institution_id=b.id)
    db.session.add_all(list(users.values()) + [mosq, kelly, room, room_b]); db.session.flush()
    kit, kit_b = Kit(name="Kit General", institution_id=a.id), Kit(name="Kit B", institution_id=b.id)
    station, station_b = CaptureStation(name="S", room_id=room.id), CaptureStation(name="SB", room_id=room_b.id)
    db.session.add_all([kit, kit_b, station, station_b]); db.session.flush()
    db.session.add_all([KitItem(kit_id=kit.id, family_id=mosq.id, quantity=8), KitItem(kit_id=kit.id, family_id=kelly.id, quantity=6),
                        ProcedureKit(procedure_type_id=proc.id, kit_id=kit.id, is_default=True),
                        ProcedurePhase(procedure_type_id=proc.id, phase_id=phase.id, sort_order=1)])
    op = Operation(status_id=sched.id, procedure_type_id=proc.id, room_id=room.id, institution_id=a.id)
    model = YoloModel(version_tag="v1-test", active=True)
    db.session.add_all([op, model]); db.session.flush()
    db.session.add_all([ModelClass(model_id=model.id, family_id=mosq.id, yolo_class_id=0), ModelClass(model_id=model.id, family_id=kelly.id, yolo_class_id=1)])
    db.session.commit()
    I.update({k: u.id for k, u in users.items()}, a=a.id, b=b.id, kit=kit.id, kit_b=kit_b.id, station=station.id,
             station_b=station_b.id, op_id=op.id, phase=phase.id, mosq=mosq.id, kelly=kelly.id)

CURRENT = {}
routes._current_user = lambda: CURRENT["user"]
client = flask_app.test_client()
ROLE = {"op": "operator_cde", "op_b": "operator_cde", "sup": "supervisor_quality", "sup_b": "supervisor_quality", "adm": "it_admin"}
DEMO = ("WS-026", "WS-001", "94.2", "18 min", "TEMPORARY DEMO DATA", "Daniel Brooks", "Alex Morgan", "v0.4.2", "Delivery Kit")


def as_user(key):
    CURRENT["user"] = fake_user(I[key], I["b"] if key.endswith("_b") else I["a"], ROLE[key], key)


def page(url):
    r = client.get(url)
    return r.status_code, r.get_data(as_text=True)


def metrics(inst):
    with flask_app.app_context():
        return analytics.indicators(inst)


PAGES = {"op": ["/operator/dashboard"], "sup": ["/supervisor/dashboard", "/supervisor/reports", "/supervisor/indicators", "/supervisor/audit-log"],
         "adm": ["/admin/dashboard", "/admin/audit-log"]}

print("== 1/26/28. empty database")
for role, urls in PAGES.items():
    as_user(role)
    for url in urls:
        status, body = page(url)
        check(status == 200 and not any(d in body for d in DEMO), f"{url} empty DB: 200, no demo")
m = metrics(I["a"])
check(m["sessions"]["total"] == 0 and m["discrepancies"]["total"] == 0 and m["agreement"]["label"] == "N/A"
      and m["resolution"]["label"] == "N/A" and m["corrections"]["total"] == 0, "14/16. zero history -> 0 / N/A, no ZeroDivisionError")
as_user("sup")
check("N/A" in page("/supervisor/indicators")[1], "indicators show N/A")


def new_session(key="op"):
    as_user(key)
    r = client.post("/operator/sessions/new", data={"operation_id": str(I["op_id"]), "kit_id": str(I["kit"]),
                                                    "capture_station_id": str(I["station"]), "counting_phase": str(I["phase"]),
                                                    "instrument_readiness_confirmed": "on"})
    return uuid.UUID(re.search(r"/operator/sessions/([0-9a-f-]{36})/capture", r.headers["Location"]).group(1))


def capture(ws):
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + os.urandom(300) + b"\xff\xd9"
    client.post(f"/operator/sessions/{ws}/capture", data={"capture_image": (io.BytesIO(jpeg), "t.jpg", "image/jpeg")},
                content_type="multipart/form-data")


def infer(ws, rows):
    client.post(f"/operator/sessions/{ws}/ai-detection", data={
        "inference_run_id": str(uuid.uuid4()), "yolo_class_id[]": [str(x[0]) for x in rows],
        "detected_quantity[]": [str(x[1]) for x in rows], "confidence[]": [str(x[2]) for x in rows]})


def validate(ws, values):
    with flask_app.app_context():
        auto = db.session.execute(select(CatEventType.id).where(CatEventType.code == "auto_count")).scalar_one()
        ev = {e.family_id: e.id for e in db.session.execute(select(CountEvent).where(CountEvent.session_id == ws, CountEvent.event_type_id == auto)).scalars()}
    client.post(f"/operator/sessions/{ws}/validation", data={
        "validation_batch_id": str(uuid.uuid4()), "event_id[]": [str(ev[f]) for f in values],
        "final_quantity[]": [str(values[f][0]) for f in values], "justification[]": [values[f][1] for f in values]})


print("== full flow in A: 1 session, 1 shortage, 1 correction, approve, close")
ws = new_session(); capture(ws); infer(ws, [(0, 7, 0.91), (1, 6, 0.95)])
validate(ws, {I["mosq"]: (8, "Recount found 8"), I["kelly"]: (6, "")})
with flask_app.app_context():
    disc = db.session.execute(select(Discrepancy).where(Discrepancy.session_id == ws)).scalar_one()
as_user("sup")
client.post(f"/supervisor/discrepancies/{disc.id}/approve")
as_user("op")
check(client.post(f"/operator/sessions/{ws}/close").status_code == 302, "flow closed")
m = metrics(I["a"])
check(m["sessions"]["total"] == 1 and m["discrepancies"]["total"] == 1 and m["discrepancies"]["resolved"] == 1
      and m["corrections"]["total"] == 1, "29. Sessions=1, Discrepancies=1, Resolved=1, Corrections=1")
check(m["agreement"]["reviewed"] == 2 and m["agreement"]["matched"] == 1 and m["agreement"]["label"] == "50.0%",
      "12/13. agreement AI vs human final: Kelly 6=6 agree, Mosquito 7≠8 disagree -> 50.0%")
check(m["resolution"]["resolved_count"] == 1 and m["resolution"]["label"] != "N/A", "resolution time computed")

with flask_app.app_context():
    d = db.session.get(Discrepancy, disc.id)
    origin = db.session.get(CountEvent, d.origin_event_id)
    d.resolved_at = origin.occurred_at + timedelta(minutes=90); db.session.commit()
check(metrics(I["a"])["resolution"]["label"] == "1.5 h", "15. average resolution = resolved_at - origin event time (90 min -> 1.5 h)")

print("== more sessions: open, counting, validating with unidentified + low confidence")
new_session()                                         # open
ws_c = new_session(); capture(ws_c)                   # counting
ws_v = new_session(); capture(ws_v); infer(ws_v, [(0, 8, 0.50), (1, 6, 0.95), (7, 1, 0.90)])  # validating
m = metrics(I["a"])
s = m["sessions"]
check((s["total"], s["open"], s["counting"], s["validating"], s["closed"]) == (5 - 1, 1, 1, 1, 1), f"6. status counts {s['by_status']}")
check(s["today"] == 4, "5. sessions today (UTC)")
check(s["with_discrepancies"] == 2 and s["without_discrepancies"] == 2 and s["with_corrections"] == 1, "session discrepancy/correction splits")
dm = m["discrepancies"]
check(dm["total"] == 3 and dm["unresolved"] == 2 and dm["resolved"] == 1, "7/8. total / unresolved / resolved")
check(dm["shortage"] == 1 and dm["low_confidence"] == 1 and dm["unidentified"] == 1 and dm["surplus"] == 0, "9. by reason (catalog codes)")
fam = {row["label"]: row["value"] for row in dm["by_family"]}
check(fam == {"Mosquito": 2, "Unidentified": 1}, f"10. by family incl. Unidentified: {fam}")
check(m["corrections"]["total"] == 1, "11. corrections from HumanCorrection only (confirmations excluded)")

print("== institution B activity must not leak")
ws_b_id = None
with flask_app.app_context():
    open_status = db.session.execute(select(CatSessionStatus).where(CatSessionStatus.code == "validating")).scalar_one()
    shortage = db.session.execute(select(CatDiscrepancyReason).where(CatDiscrepancyReason.code == "shortage")).scalar_one()
    ws_b = WorkSession(status_id=open_status.id, user_id=I["op_b"], station_id=I["station_b"], kit_id=I["kit_b"])
    db.session.add(ws_b); db.session.flush()
    db.session.add(Discrepancy(session_id=ws_b.id, description="B only", resolved=False, reason_id=shortage.id, family_id=I["kelly"]))
    db.session.add(AccessAudit(actor_type="user", actor_user_id=I["op_b"], action="CREATE_SESSION", resource_type="work_session",
                               resource_id=ws_b.id, institution_id=I["b"]))
    db.session.add(AccessAudit(actor_type="user", actor_user_id=I["adm"], action="CREATE_KIT", resource_type="kit",
                               resource_id=I["kit"], institution_id=I["a"]))
    db.session.commit()
    ws_b_id = ws_b.id
m2 = metrics(I["a"])
check(m2["sessions"]["total"] == 4 and m2["discrepancies"]["total"] == 3 and "Kelly" not in {r["label"] for r in m2["discrepancies"]["by_family"]},
      "23/33. A metrics exclude B")
check(metrics(I["b"])["sessions"]["total"] == 1 and metrics(I["b"])["discrepancies"]["total"] == 1, "B metrics own data only")

print("== 2/3/4. dashboards with data")
as_user("op")
status, body = page("/operator/dashboard")
check(status == 200 and "Sessions in Counting" in body and f"/operator/sessions/{ws}" in body or str(ws_v) in body, "operator dashboard real + UUID links")
check(str(ws_b_id)[:8].upper() not in body, "operator dashboard no B session")
as_user("sup")
status, body = page("/supervisor/dashboard")
check(status == 200 and "Top Instrument Families with Discrepancies" in body and "Mosquito" in body and "B only" not in body, "supervisor dashboard real")
as_user("adm")
status, body = page("/admin/dashboard")
check(status == 200 and "50.0%" in body and "1.5 h" in body and "(global)" in body, "admin dashboard agreement + resolution real")
check("94.2" not in body and "18 min" not in body, "19/20. no 94.2% / 18 min")

print("== 17/18. reports & indicators")
as_user("sup")
status, body = page("/supervisor/indicators")
check(status == 200 and "50.0%" in body and "Mosquito" in body and "Unidentified" in body and not any(d in body for d in DEMO), "indicators real")
status, body = page("/supervisor/reports")
check(status == 200 and "Discrepancies by Reason" in body and "Shortage against expected inventory" in body and not any(d in body for d in DEMO), "reports real")
body_closed = page("/supervisor/reports?session_status=closed")[1]
check(str(ws)[:8].upper() in body_closed and str(ws_v)[:8].upper() not in body_closed.split("Closed Sessions")[0], "35. report status filter")
body_today = page("/supervisor/reports?period=today&operator=" + str(I["op"]))[1]
check("Kit General" in body_today, "35. report period/operator filters")
check(page("/supervisor/reports?kit=" + str(uuid.uuid4()))[1].count("No sessions in this selection") == 2, "35. unknown kit filter -> empty, no error")

print("== 21/22/34. audit logs")
as_user("adm")
body = page("/admin/audit-log")[1]
check("CREATE_KIT" in body and "CREATE_SESSION" in body and "APPROVE_DISCREPANCY" in body, "21. admin audit real")
filtered = page("/admin/audit-log?event_type=CLOSE_SESSION")[1]
check("CLOSE_SESSION" in filtered and "CREATE_KIT</strong>" not in filtered, "35. admin audit event filter")
check("No audit events" in page("/admin/audit-log?outcome=denied&event_type=CREATE_KIT")[1], "35. outcome filter")
check("CAPTURE_EVIDENCE</strong>" in page("/admin/audit-log?search=capture")[1], "35. search filter")
as_user("sup")
body = page("/supervisor/audit-log")[1]
check("VISION_RESULT" in body and "HUMAN_CORRECTION" in body and "CREATE_KIT</strong>" not in body, "22. supervisor audit clinical only")
check(body.count("CREATE_SESSION</strong>") == 4, "34. supervisor audit excludes B's CREATE_SESSION")
as_user("sup_b")
check("Mosquito" not in page("/supervisor/indicators")[1], "33. B supervisor sees no A data")

print("== 29/30/31. templates")
tpl_root = os.path.join(APP, "templates")
check(not os.path.exists(os.path.join(tpl_root, "operator", "sessions", "discrepancy.html"))
      and not os.path.exists(os.path.join(tpl_root, "components", "modals", "supervisor_review_required.html")), "30. dead templates removed")
offenders = []
for folder, _, files in os.walk(tpl_root):
    for name in files:
        rel = os.path.relpath(os.path.join(folder, name), tpl_root).replace("\\", "/")
        if rel == "operator/sessions/active.html":  # devtools-only preview template, not served by the app
            continue
        text_ = open(os.path.join(folder, name), encoding="utf-8").read()
        for needle in ("WS-026", "TEMPORARY DEMO DATA", "94.2", "18 min", 'href="#"', 'action="#"'):
            if needle in text_:
                offenders.append(f"{rel}:{needle}")
check(not offenders, f"29/31. no demo markers / dead links in served templates {offenders}")

print("== 32. mandatory audit events have producers")
sources = ""
for folder in (os.path.join(APP, "services"), os.path.join(APP, "controllers"),
               os.path.join(os.path.dirname(APP), "BackendAuthService")):
    for fname in os.listdir(folder):
        if fname.endswith(".py"):
            sources += open(os.path.join(folder, fname), encoding="utf-8").read()
producers = {
    "LOGIN": r'audit_access\(connection, "LOGIN"', "LOGOUT": r'audit_access\(connection, "LOGOUT"',
    "CREATE_INSTRUMENT/UPDATE_INSTRUMENT": r"audit_save\(ctx, instrument, 'INSTRUMENT'", "CREATE_KIT": r"audit_save\(ctx, kit, 'KIT'",
    "CREATE_SESSION": r"'CREATE_SESSION'", "START_COUNT": r"'START_COUNT'", "VISION_RESULT": r"'VISION_RESULT'",
    "CREATE_DISCREPANCY": r"'CREATE_DISCREPANCY'", "HUMAN_CORRECTION": r"'HUMAN_CORRECTION'", "SUPERVISOR_REVIEW": r"'SUPERVISOR_REVIEW'",
    "APPROVE_DISCREPANCY": r"'APPROVE_DISCREPANCY'", "CLOSE_SESSION": r"'CLOSE_SESSION'",
}
missing = [event for event, pattern in producers.items() if not re.search(pattern, sources)]
check(not missing, f"all mandatory events have a producer {missing}")
with flask_app.app_context():
    produced = set(db.session.execute(select(AccessAudit.action)).scalars())
check({"CREATE_SESSION", "START_COUNT", "VISION_RESULT", "CREATE_DISCREPANCY", "HUMAN_CORRECTION", "SUPERVISOR_REVIEW",
       "APPROVE_DISCREPANCY", "CLOSE_SESSION"} <= produced, "clinical events actually produced by the flow")

# LOGIN / LOGOUT audit checks live in apps/BackendAuthService/tests/scenarios/auth_audit.py
shutil.rmtree(STORE, ignore_errors=True)
finish()
