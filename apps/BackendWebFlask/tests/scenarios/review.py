"""End-to-end: human validation -> supervisor review -> correction loop -> close."""
import io
import os
import re
import shutil
import uuid

from _common import APP, HERE, check, fake_user, finish

STORE = os.path.join(HERE, "evidence_review")
shutil.rmtree(STORE, ignore_errors=True)
os.environ["CAPTURE_STORAGE_ROOT"] = STORE
os.environ["VISION_INFERENCE_PROVIDER"] = "controlled"

from _common import setup_app  # noqa: E402

flask_app, db = setup_app("smoke_review.db")

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
from models.HumanCorrection import HumanCorrection  # noqa: E402
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
from services import review_service  # noqa: E402

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
             (("op", "Oper A", a), ("sup", "Super A", a), ("op_b", "Oper B", b), ("sup_b", "Super B", b))}
    mosq, kelly = InstrumentFamily(code="MOS", name="Mosquito", category_id=cat.id), InstrumentFamily(code="KEL", name="Kelly", category_id=cat.id)
    room = OperatingRoom(code="R", name="R", institution_id=a.id)
    db.session.add_all(list(users.values()) + [mosq, kelly, room]); db.session.flush()
    kit = Kit(name="Kit General", institution_id=a.id); station = CaptureStation(name="S", room_id=room.id)
    db.session.add_all([kit, station]); db.session.flush()
    db.session.add_all([KitItem(kit_id=kit.id, family_id=mosq.id, quantity=8), KitItem(kit_id=kit.id, family_id=kelly.id, quantity=6),
                        ProcedureKit(procedure_type_id=proc.id, kit_id=kit.id, is_default=True),
                        ProcedurePhase(procedure_type_id=proc.id, phase_id=phase.id, sort_order=1)])
    op = Operation(status_id=sched.id, procedure_type_id=proc.id, room_id=room.id, institution_id=a.id)
    model = YoloModel(version_tag="v1-test", active=True)
    db.session.add_all([op, model]); db.session.flush()
    db.session.add_all([ModelClass(model_id=model.id, family_id=mosq.id, yolo_class_id=0), ModelClass(model_id=model.id, family_id=kelly.id, yolo_class_id=1)])
    db.session.commit()
    I.update({k: u.id for k, u in users.items()}, a=a.id, b=b.id, kit=kit.id, station=station.id, op_id=op.id, phase=phase.id,
             mosq=mosq.id, kelly=kelly.id)

CURRENT = {}
routes._current_user = lambda: CURRENT["user"]
client = flask_app.test_client()


def as_user(key):
    role = "supervisor_quality" if key.startswith("sup") else "operator_cde"
    CURRENT["user"] = fake_user(I[key], I["b"] if key.endswith("_b") else I["a"], role, key)


def q(model, **f):
    with flask_app.app_context():
        return db.session.execute(select(model).filter_by(**f)).scalars().all()


def count(model, **f):
    with flask_app.app_context():
        return db.session.scalar(select(func.count()).select_from(model).filter_by(**f))


def etype(code):
    with flask_app.app_context():
        return db.session.execute(select(CatEventType.id).where(CatEventType.code == code)).scalar_one()


def status_of(ws_id):
    with flask_app.app_context():
        return db.session.get(CatSessionStatus, db.session.get(WorkSession, ws_id).status_id).code


def ready_session(rows):
    as_user("op")
    r = client.post("/operator/sessions/new", data={"operation_id": str(I["op_id"]), "kit_id": str(I["kit"]),
                                                    "capture_station_id": str(I["station"]), "counting_phase": str(I["phase"]),
                                                    "instrument_readiness_confirmed": "on"})
    ws_id = uuid.UUID(re.search(r"/operator/sessions/([0-9a-f-]{36})/capture", r.headers["Location"]).group(1))
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + os.urandom(300) + b"\xff\xd9"
    client.post(f"/operator/sessions/{ws_id}/capture", data={"capture_image": (io.BytesIO(jpeg), "t.jpg", "image/jpeg")},
                content_type="multipart/form-data")
    client.post(f"/operator/sessions/{ws_id}/ai-detection", data={
        "inference_run_id": str(uuid.uuid4()), "yolo_class_id[]": [str(x[0]) for x in rows],
        "detected_quantity[]": [str(x[1]) for x in rows], "confidence[]": [str(x[2]) for x in rows]})
    return ws_id


def auto_events(ws_id):
    with flask_app.app_context():
        return {e.family_id: e for e in db.session.execute(select(CountEvent).where(
            CountEvent.session_id == ws_id, CountEvent.event_type_id == etype("auto_count"))).scalars()}


def manual_events(ws_id):
    with flask_app.app_context():
        return db.session.execute(select(CountEvent).where(CountEvent.session_id == ws_id, CountEvent.event_type_id == etype("manual_count"))
                                  .order_by(CountEvent.occurred_at)).scalars().all()


def validate(ws_id, values, batch=None, url="validation"):
    ev = auto_events(ws_id)
    batch = batch or uuid.uuid4()
    fams = [f for f in values]
    return client.post(f"/operator/sessions/{ws_id}/{url}", data={
        "validation_batch_id": str(batch), "event_id[]": [str(ev[f].id) for f in fams],
        "final_quantity[]": [str(values[f][0]) for f in fams], "justification[]": [values[f][1] for f in fams]}), batch


def the_discrepancy(ws_id, family):
    return [d for d in q(Discrepancy, session_id=ws_id) if d.family_id == family]


def state(d_id):
    with flask_app.app_context():
        return review_service.get_discrepancy_workflow_state(db.session.get(Discrepancy, d_id))


print("== scenario 1: AI 7 vs expected 8")
ws = ready_session([(0, 7, 0.91), (1, 6, 0.95)])
check(status_of(ws) == "validating", "session validating after inference")
disc = the_discrepancy(ws, I["mosq"])[0]
check(state(disc.id) == "OPEN", "22. OPEN before human validation")
body = client.get(f"/operator/sessions/{ws}/validation").get_data(as_text=True)
check('name="final_quantity[]"' in body and 'value="7"' in body and "v1-test" in body and "WS-026" not in body, "validation page real, initial = AI value")
r = client.get(f"/operator/sessions/{ws}/validation-summary")
check(r.status_code == 302 and "/validation" in r.headers["Location"], "9. summary blocked until validated")
r, _ = validate(ws, {I["mosq"]: (8, "Found one under drape")})
check(r.status_code == 400 and not manual_events(ws), "9. incomplete validation rejected")
r, _ = validate(ws, {I["mosq"]: (8, ""), I["kelly"]: (6, "")})
check(r.status_code == 400 and not manual_events(ws), "7. justification required when value changes")
r, _ = validate(ws, {I["mosq"]: (-1, "x"), I["kelly"]: (6, "")})
check(r.status_code == 400, "negative count rejected")
as_user("op_b")
r, _ = validate(ws, {I["mosq"]: (8, "x"), I["kelly"]: (6, "")})
check(r.status_code == 404, "40. other-institution operator cannot validate")
as_user("op")
r, batch = validate(ws, {I["mosq"]: (8, "Found one under drape"), I["kelly"]: (6, "")})
check(r.status_code == 302 and r.headers["Location"].endswith("/validation-summary"), "validation stored -> summary")
r, _ = validate(ws, {I["mosq"]: (8, "Found one under drape"), I["kelly"]: (6, "")}, batch=batch)
man = manual_events(ws)
check(r.status_code == 302 and len(man) == 2, "8. same validation_batch_id -> no duplicates")
auto = auto_events(ws)
check(auto[I["mosq"]].detected_quantity == 7 and auto[I["mosq"]].user_id is None, "1. auto_count untouched (7)")
m_mosq = next(e for e in man if e.family_id == I["mosq"]); m_kel = next(e for e in man if e.family_id == I["kelly"])
check(m_mosq.detected_quantity == 8 and m_mosq.user_id == I["op"] and m_mosq.payload["validation_type"] == "correction"
      and m_mosq.payload["reviewed_event_id"] == str(auto[I["mosq"]].id) and m_mosq.payload["previous_quantity"] == 7, "4. correction manual_count")
check(m_kel.payload["validation_type"] == "confirmation" and m_kel.detected_quantity == 6, "2. confirmation manual_count")
hcs = q(HumanCorrection)
check(len(hcs) == 1 and hcs[0].count_event_id == auto[I["mosq"]].id and hcs[0].justification == "Found one under drape", "3/5. HC only for correction, points to auto_count")
check(count(AccessAudit, action="HUMAN_CORRECTION") == 1 and count(AccessAudit, action="HUMAN_CONFIRMATION") == 1, "human audits")
check(the_discrepancy(ws, I["mosq"])[0].resolved is False, "13. correction does not resolve discrepancy")
check(state(disc.id) == "UNDER_REVIEW", "23. UNDER_REVIEW after human result")
body = client.get(f"/operator/sessions/{ws}/validation-summary").get_data(as_text=True)
check("Found one under drape" in body and "AI 7 ≠ Human 8" in body and "Mosquito" in body, "10/11. summary shows AI vs human separately")
check(count(CountEvent, event_type_id=etype("validation_passed")) == 0, "validation_passed not claimed with open discrepancy")

print("== supervisor")
as_user("sup")
body = client.get("/supervisor/discrepancies").get_data(as_text=True)
check("Mosquito" in body and "Under Review" in body and "WS-026" not in body, "16. supervisor discrepancy list real")
body = client.get(f"/supervisor/discrepancies/{ws}/review").get_data(as_text=True)
check(f'/supervisor/discrepancies/{disc.id}/approve' in body and f'/supervisor/discrepancies/{disc.id}/request-correction' in body
      and 'method="post"' in body, "39. modals post to real endpoints")
media = re.search(r'src="(/supervisor/sessions/[^"]+/media/[^"]+)"', body)
check(media is not None and client.get(media.group(1)).status_code == 200, "15. supervisor sees evidence")
as_user("sup_b")
check(client.get(f"/supervisor/discrepancies/{ws}/review").status_code == 404, "14. other supervisor -> 404")
check(client.get(media.group(1)).status_code == 404, "16. other institution evidence rejected")
check(client.post(f"/supervisor/discrepancies/{disc.id}/approve").status_code == 404, "40. cross-institution approve -> 404")
check("Mosquito" not in client.get("/supervisor/discrepancies").get_data(as_text=True)
      and str(ws)[:8].upper() not in client.get("/supervisor/sessions").get_data(as_text=True), "14. lists scoped")
check(count(AccessAudit, outcome="denied") >= 1, "cross-institution denied audited")
as_user("sup")
r = client.post(f"/supervisor/discrepancies/{disc.id}/request-correction", data={"note": ""})
check(r.status_code == 400, "note required")
r = client.post(f"/supervisor/discrepancies/{disc.id}/request-correction", data={"note": "Recount under the tray, image shows 7"})
check(r.status_code == 302, "request correction stored")
req = q(CountEvent, event_type_id=etype("correction_requested"))
check(len(req) == 1 and req[0].payload["note"] == "Recount under the tray, image shows 7" and req[0].payload["decision"] == "request_correction"
      and req[0].user_id == I["sup"] and req[0].payload["reviewed_human_event_id"] == str(m_mosq.id), "20. note persisted")
check(the_discrepancy(ws, I["mosq"])[0].resolved is False, "19. request correction does not resolve")
check(state(disc.id) == "CORRECTION_REQUIRED", "24. CORRECTION_REQUIRED")
check(count(AccessAudit, action="REQUEST_CORRECTION") == 1 and count(AccessAudit, action="SUPERVISOR_REVIEW") == 1, "request audited")
check(client.post(f"/supervisor/discrepancies/{disc.id}/approve").status_code == 409, "cannot approve while correction pending")

print("== operator correction loop")
as_user("op")
body = client.get(f"/operator/sessions/{ws}/correction").get_data(as_text=True)
check("Recount under the tray" in body and "Super A" in body and "WS-026" not in body, "correction page real")
r, _ = validate(ws, {I["mosq"]: (7, "Recounted: 7 pieces")}, url="correction")
check(r.status_code == 302, "correction submitted")
man = manual_events(ws)
m2 = man[-1]
check(len(man) == 3 and m2.payload["reviewed_event_id"] == str(m_mosq.id) and m2.detected_quantity == 7, "second manual_count chains to first")
hc2 = [h for h in q(HumanCorrection) if h.justification == "Recounted: 7 pieces"]
check(len(hc2) == 1 and hc2[0].count_event_id == m_mosq.id, "6. second HC points to previous manual_count")
check(state(disc.id) == "UNDER_REVIEW", "26. back to UNDER_REVIEW")
check(len(the_discrepancy(ws, I["mosq"])) == 1, "no duplicate shortage discrepancy")

print("== blocked close")
r = client.post(f"/operator/sessions/{ws}/close")
check(r.status_code == 409, "27. unresolved discrepancy blocks close")
check(status_of(ws) == "validating", "28. status unchanged")
check(count(CountEvent, event_type_id=etype("close_blocked")) == 1 and count(AccessAudit, action="CLOSE_SESSION", outcome="denied") == 1,
      "29. close_blocked + denied audit persisted")

as_user("sup")
client.post(f"/supervisor/discrepancies/{disc.id}/reject", data={"note": "Still inconsistent with evidence"})
check(state(disc.id) == "CORRECTION_REQUIRED" and not the_discrepancy(ws, I["mosq"])[0].resolved, "21. reject does not resolve")
check(count(AccessAudit, action="REJECT_DISCREPANCY") == 1, "reject audited")
as_user("op")
validate(ws, {I["mosq"]: (8, "Third count confirms 8")}, url="correction")
as_user("sup")
r = client.post(f"/supervisor/discrepancies/{disc.id}/approve", data={"note": "OK"})
d = the_discrepancy(ws, I["mosq"])[0]
check(r.status_code == 302 and d.resolved and d.resolved_at is not None, "17. approve -> resolved + resolved_at")
check(state(disc.id) == "APPROVED" and count(AccessAudit, action="APPROVE_DISCREPANCY") == 1, "18/25. APPROVED + audited")
check(auto_events(ws)[I["mosq"]].detected_quantity == 7 and len(manual_events(ws)) == 4 and len(q(HumanCorrection)) == 3,
      "29. full history intact (auto 7, 4 manual, 3 HC)")

print("== close")
as_user("op")
body = client.get(f"/operator/sessions/{ws}/ready-to-close").get_data(as_text=True)
check("Ready to close" in body and 'method="post"' in body, "ready to close page real")
r = client.post(f"/operator/sessions/{ws}/close")
with flask_app.app_context():
    w = db.session.get(WorkSession, ws)
    ended, closed_by = w.ended_at, w.closed_by_user_id
check(r.status_code == 302 and status_of(ws) == "closed", "30/31. closed")
check(ended is not None and closed_by == I["op"], "32/33. ended_at + closed_by")
closes = q(CountEvent, event_type_id=etype("session_close"))
check(len(closes) == 1 and closes[0].payload["human_corrections_count"] == 3 and closes[0].payload["resolved_discrepancies_count"] == 1,
      "34. session_close event with summary")
check(count(AccessAudit, action="CLOSE_SESSION", outcome="success") == 1, "35. CLOSE_SESSION audited")
r = client.post(f"/operator/sessions/{ws}/close")
with flask_app.app_context():
    check(len(q(CountEvent, event_type_id=etype("session_close"))) == 1 and db.session.get(WorkSession, ws).ended_at == ended, "36. second close no-op")
body = client.get(f"/operator/sessions/closed/{ws}").get_data(as_text=True)
check("Third count confirms 8" in body and "Recount under the tray" in body and "Approved" in body and "CLOSE_SESSION" in body
      and "Oper A" in body and "v1-test" in body, "37. closed details real")
for needle in ("Third count confirms 8", "Recount under the tray", "Approved", "CLOSE_SESSION", "Oper A", "v1-test"):
    print("   ", needle, needle in body)
check(r.status_code == 302, "second close redirects")

print("== scenario 2: AI 8 = expected, human 7")
ws2 = ready_session([(0, 8, 0.95), (1, 6, 0.95)])
check(not q(Discrepancy, session_id=ws2), "no AI discrepancy")
validate(ws2, {I["mosq"]: (7, "One missing on recount"), I["kelly"]: (6, "")})
d2 = q(Discrepancy, session_id=ws2)
man2 = manual_events(ws2)
check(len(d2) == 1 and d2[0].origin_event_id == next(e.id for e in man2 if e.family_id == I["mosq"]) and not d2[0].resolved, "12. human mismatch creates discrepancy from manual_count")
check(state(d2[0].id) == "UNDER_REVIEW", "human-origin discrepancy under review")

print("== scenario 3: everything matches")
ws3 = ready_session([(0, 8, 0.95), (1, 6, 0.95)])
validate(ws3, {I["mosq"]: (8, ""), I["kelly"]: (6, "")})
check(count(CountEvent, session_id=ws3, event_type_id=etype("validation_passed")) == 1, "validation_passed only when no discrepancies")
check(not q(HumanCorrection) or all(h.count_event_id not in {e.id for e in auto_events(ws3).values()} for h in q(HumanCorrection)), "3. confirmations create no HC")
r = client.post(f"/operator/sessions/{ws3}/close")
check(r.status_code == 302 and status_of(ws3) == "closed", "clean session closes")

print("== 38. demo removed from finished screens")
finished = ["operator/sessions/validation.html", "operator/sessions/validation_summary.html", "operator/sessions/awaiting_review.html",
            "operator/sessions/correction_requested.html", "operator/sessions/ready_to_close.html", "operator/sessions/closed_details.html",
            "supervisor/dashboard.html", "supervisor/sessions/list.html", "supervisor/sessions/history.html", "supervisor/sessions/details.html",
            "supervisor/discrepancies/list.html", "supervisor/discrepancies/review.html", "components/modals/approve_review.html",
            "components/modals/request_correction.html", "components/modals/reject_review.html", "components/modals/close_session.html"]
for rel in finished:
    text_ = open(os.path.join(APP, "templates", rel), encoding="utf-8").read()
    check(not any(s in text_ for s in ("WS-026", "WS-02", "TEMPORARY DEMO DATA", "not implemented", "frontend simulation only", 'href="#"')), f"clean {rel}")
as_user("sup")
for url in ("/supervisor/dashboard", "/supervisor/sessions", "/supervisor/sessions/history", f"/supervisor/sessions/{ws}"):
    body = client.get(url).get_data(as_text=True)
    check(client.get(url).status_code == 200 and "WS-026" not in body, f"supervisor page {url}")
check(str(ws)[:8].upper() in client.get("/supervisor/sessions/history").get_data(as_text=True), "closed session in history")

shutil.rmtree(STORE, ignore_errors=True)
finish()
