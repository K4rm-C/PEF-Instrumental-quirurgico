"""Controlled inference -> CountEvent auto_count -> Discrepancy -> counting->validating tests."""
import io
import os
import re
import shutil
import uuid

from _common import APP, HERE, check, fake_user, finish

STORE = os.path.join(HERE, "evidence_vision")
shutil.rmtree(STORE, ignore_errors=True)
os.environ["CAPTURE_STORAGE_ROOT"] = STORE
os.environ["VISION_INFERENCE_PROVIDER"] = "controlled"
os.environ["VISION_CONFIDENCE_THRESHOLD"] = "0.70"

from _common import setup_app  # noqa: E402

flask_app, db = setup_app("smoke_vision.db")

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

import controllers.routes as routes  # noqa: E402
import services.vision_service as vision  # noqa: E402
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

I = {}
with flask_app.app_context():
    a, b = Institution(name="A"), Institution(name="B")
    db.session.add_all([a, b]); db.session.flush()
    db.session.add_all([CatSessionStatus(code=c, name=c) for c in ("open", "counting", "validating", "closed")])
    db.session.add(CatEventType(code="auto_count", name="Model-suggested count"))
    db.session.add_all([CatDiscrepancyReason(code=c, name=c) for c in ("shortage", "surplus", "unidentified", "low_confidence", "misclassified")])
    sched = CatOperationStatus(code="scheduled", name="Scheduled")
    cat = CatInstrumentCategory(code="c", name="c")
    phase = CatOperationPhase(code="pre", name="Pre")
    proc = CatProcedureType(code="P", name="Proc")
    db.session.add_all([sched, cat, phase, proc]); db.session.flush()
    op_user = User(name="Op", email="op@a.org", password_hash="x", institution_id=a.id)
    op_b = User(name="OpB", email="op@b.org", password_hash="x", institution_id=b.id)
    mosq, kelly, allis, fara = (InstrumentFamily(code=c, name=n, category_id=cat.id) for c, n in
                                (("MOS", "Mosquito"), ("KEL", "Kelly"), ("ALL", "Allis"), ("FAR", "Farabeuf")))
    room = OperatingRoom(code="R", name="R", institution_id=a.id)
    db.session.add_all([op_user, op_b, mosq, kelly, allis, fara, room]); db.session.flush()
    kit = Kit(name="Kit", institution_id=a.id)
    station = CaptureStation(name="S", room_id=room.id)
    db.session.add_all([kit, station]); db.session.flush()
    db.session.add_all([KitItem(kit_id=kit.id, family_id=mosq.id, quantity=8), KitItem(kit_id=kit.id, family_id=kelly.id, quantity=6),
                        KitItem(kit_id=kit.id, family_id=allis.id, quantity=2),
                        ProcedureKit(procedure_type_id=proc.id, kit_id=kit.id, is_default=True),
                        ProcedurePhase(procedure_type_id=proc.id, phase_id=phase.id, sort_order=1)])
    op = Operation(status_id=sched.id, procedure_type_id=proc.id, room_id=room.id, institution_id=a.id)
    model = YoloModel(version_tag="v1-test", active=True)
    db.session.add_all([op, model]); db.session.flush()
    db.session.add_all([ModelClass(model_id=model.id, family_id=f.id, yolo_class_id=i) for i, f in enumerate((mosq, kelly, allis, fara))])
    db.session.commit()
    I.update(a=a.id, b=b.id, op_user=op_user.id, op_b=op_b.id, kit=kit.id, station=station.id, op=op.id, phase=phase.id,
             mosq=mosq.id, kelly=kelly.id, allis=allis.id, fara=fara.id, model=model.id)

CURRENT = {"user": fake_user(I["op_user"], I["a"], "operator_cde", "Op")}
routes._current_user = lambda: CURRENT["user"]
client = flask_app.test_client()


def count(model, **filters):
    with flask_app.app_context():
        return db.session.scalar(select(func.count()).select_from(model).filter_by(**filters))


def status_of(ws_id):
    with flask_app.app_context():
        return db.session.get(CatSessionStatus, db.session.get(WorkSession, ws_id).status_id).code


def new_session(capture=True):
    r = client.post("/operator/sessions/new", data={
        "operation_id": str(I["op"]), "kit_id": str(I["kit"]), "capture_station_id": str(I["station"]),
        "counting_phase": str(I["phase"]), "instrument_readiness_confirmed": "on"})
    ws_id = uuid.UUID(re.search(r"/operator/sessions/([0-9a-f-]{36})/capture", r.headers["Location"]).group(1))
    if capture:
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + os.urandom(400) + b"\xff\xd9"
        client.post(f"/operator/sessions/{ws_id}/capture", data={"capture_image": (io.BytesIO(jpeg), "t.jpg", "image/jpeg")},
                    content_type="multipart/form-data")
    return ws_id


def infer(ws_id, rows, run_id=None):
    run_id = run_id or uuid.uuid4()
    data = {"inference_run_id": str(run_id), "yolo_class_id[]": [str(r[0]) for r in rows],
            "detected_quantity[]": [str(r[1]) for r in rows], "confidence[]": [str(r[2]) for r in rows]}
    return client.post(f"/operator/sessions/{ws_id}/ai-detection", data=data), run_id


def rename(model, old, new):
    with flask_app.app_context():
        row = db.session.execute(select(model).where(model.code == old)).scalar_one()
        row.code = new; db.session.commit()


def events_of(ws_id):
    with flask_app.app_context():
        return db.session.execute(select(CountEvent).where(CountEvent.session_id == ws_id)).scalars().all()


def discrepancies_of(ws_id):
    with flask_app.app_context():
        codes = {r.id: r.code for r in db.session.execute(select(CatDiscrepancyReason)).scalars()}
        return [(d, codes[d.reason_id]) for d in db.session.execute(select(Discrepancy).where(Discrepancy.session_id == ws_id)).scalars()]


GOOD = [(0, 7, 0.91), (1, 6, 0.95), (3, 1, 0.88), (9, 2, 0.80)]

print("== 4. no capture")
ws_open = new_session(capture=False)
r, _ = infer(ws_open, GOOD)
check(r.status_code == 409 and count(CountEvent) == 0, "open session without capture -> 409, no events")

ws = new_session()
check(status_of(ws) == "counting", "session counting after capture")

print("== 1-3. model registry")
with flask_app.app_context():
    db.session.get(YoloModel, I["model"]).active = False; db.session.commit()
r, _ = infer(ws, GOOD)
check(r.status_code == 409 and "no active vision model" in r.get_data(as_text=True), "no active model -> 409")
with flask_app.app_context():
    db.session.execute(text("DROP INDEX uk_yolo_model_active"))  # emulate an inconsistent registry
    db.session.get(YoloModel, I["model"]).active = True
    db.session.add(YoloModel(version_tag="v2-dup", active=True)); db.session.commit()
r, _ = infer(ws, GOOD)
check(r.status_code == 409 and "More than one" in r.get_data(as_text=True), "two active models -> 409")
with flask_app.app_context():
    db.session.get(YoloModel, I["model"]).active = False; db.session.commit()
r, _ = infer(ws, GOOD)
check(r.status_code == 409 and "no class mappings" in r.get_data(as_text=True), "active model without classes -> 409")
with flask_app.app_context():
    db.session.execute(select(YoloModel).where(YoloModel.version_tag == "v2-dup")).scalar_one().active = False
    db.session.get(YoloModel, I["model"]).active = True; db.session.commit()
check(count(CountEvent) == 0 and count(Discrepancy) == 0 and status_of(ws) == "counting", "no events from rejected runs")

print("== form validation")
for bad, label in (([(0, -1, 0.9)], "negative qty"), ([(0, 1, 1.5)], "confidence > 1"), ([(0, 1, 0.9), (0, 2, 0.9)], "duplicate class"),
                   ([(-2, 1, 0.9)], "negative class"), ([("x", 1, 0.9)], "non-int class"), ([], "no rows")):
    r, _ = infer(ws, bad)
    check(r.status_code == 400, f"{label} -> 400")
check(count(CountEvent) == 0, "invalid input stores nothing")

print("== 22/29/23. rollbacks")
rename(CatSessionStatus, "validating", "tmp_val")
r, _ = infer(ws, GOOD)
check(r.status_code == 409 and count(CountEvent) == 0 and status_of(ws) == "counting", "missing validating -> 409 + nothing stored")
rename(CatSessionStatus, "tmp_val", "validating")
rename(CatDiscrepancyReason, "low_confidence", "tmp_lc")
r, _ = infer(ws, GOOD)
check(r.status_code == 409 and "catalogs must be updated" in r.get_data(as_text=True) and count(CountEvent) == 0,
      "missing low_confidence catalog -> 409")
rename(CatDiscrepancyReason, "tmp_lc", "low_confidence")
original = vision.record_audit


def failing_audit(action, *args, **kwargs):
    if action == "CREATE_DISCREPANCY":
        raise SQLAlchemyError("simulated discrepancy failure")
    return original(action, *args, **kwargs)


vision.record_audit = failing_audit
r, _ = infer(ws, GOOD)
vision.record_audit = original
check(r.status_code == 503 and count(CountEvent) == 0 and count(Discrepancy) == 0 and status_of(ws) == "counting",
      "failure while creating discrepancies -> full rollback")

print("== 5-21. main run")
with flask_app.app_context():
    capture_id = str(db.session.execute(select(AccessAudit.resource_id).where(AccessAudit.action == "CAPTURE_EVIDENCE")
                                        .order_by(AccessAudit.occurred_at.desc())).first()[0])
r, run1 = infer(ws, GOOD)
check(r.status_code == 302 and r.headers["Location"].endswith(f"/operator/sessions/{ws}/ai-detection"), "redirect to AI page")
ev = events_of(ws)
by_family = {e.family_id: e for e in ev}
check(len(ev) == 5, f"5 auto_count events (3 expected + Farabeuf + unidentified): {len(ev)}")
with flask_app.app_context():
    auto = db.session.execute(select(CatEventType).where(CatEventType.code == "auto_count")).scalar_one()
check(all(e.event_type_id == auto.id for e in ev), "event type auto_count")
check(all(e.user_id is None for e in ev), "CountEvent.user_id NULL")
m = by_family[I["mosq"]]
check((m.expected_quantity, m.detected_quantity) == (8, 7), "Mosquito expected 8 detected 7 (class 0 -> family)")
check(by_family[I["allis"]].detected_quantity == 0 and by_family[I["allis"]].expected_quantity == 2, "expected but undetected -> detected 0")
check(by_family[I["fara"]].expected_quantity == 0 and by_family[I["fara"]].detected_quantity == 1, "detected not expected -> expected 0")
unk = by_family[None]
check(unk.expected_quantity is None and unk.detected_quantity == 2 and unk.payload["yolo_class_id"] == 9, "unmapped class kept as unidentified event")
p = m.payload
check(p["model_id"] == str(I["model"]) and p["model_version_tag"] == "v1-test" and p["inference_provider"] == "controlled"
      and p["confidence_threshold"] == 0.7 and p["media_asset_id"] and p["confidence_mean"] == 0.91 and p["yolo_class_id"] == 0
      and p["inference_run_id"] == str(run1), "payload has model/version/media/threshold/provider/class/confidence/run")
check(p["media_asset_id"] == capture_id, "payload media_asset_id = latest capture")
check(all(e.client_event_id is not None for e in ev) and len({e.client_event_id for e in ev}) == 5, "deterministic client_event_id per event")
ds = discrepancies_of(ws)
reasons = sorted((d.family_id == I["mosq"], d.family_id == I["allis"], d.family_id == I["fara"], d.family_id is None, code) for d, code in ds)
codes = {(d.family_id, code) for d, code in ds}
check((I["mosq"], "shortage") in codes, "8 vs 7 -> shortage")
check((I["allis"], "shortage") in codes, "2 vs 0 -> shortage")
check((I["fara"], "surplus") in codes, "0 vs 1 -> surplus")
check((None, "unidentified") in codes, "unmapped class -> unidentified")
check(not any(f == I["kelly"] for f, _ in codes), "6 vs 6 -> no discrepancy")
check(len(ds) == 4 and all(not d.resolved and d.resolved_at is None for d, _ in ds), "4 discrepancies, resolved = FALSE")
ev_ids = {e.id: e for e in ev}
check(all(d.origin_event_id in ev_ids and ev_ids[d.origin_event_id].family_id == d.family_id for d, _ in ds), "origin_event_id -> matching CountEvent")
check(status_of(ws) == "validating", "counting -> validating")
check(count(AccessAudit, action="VISION_RESULT", resource_id=ws) == 1, "VISION_RESULT audited once")
check(count(AccessAudit, action="CREATE_DISCREPANCY") == 4, "CREATE_DISCREPANCY per discrepancy")

print("== 24-25. idempotency + single run")
r, _ = infer(ws, GOOD, run_id=run1)
check(r.status_code == 302 and len(events_of(ws)) == 5 and len(discrepancies_of(ws)) == 4, "same run id resubmitted -> no duplicates")
r, _ = infer(ws, GOOD)
check(r.status_code == 409 and len(events_of(ws)) == 5, "validating session -> new run rejected")

print("== 27. AI Suggested Count page")
body = client.get(f"/operator/sessions/{ws}/ai-detection").get_data(as_text=True)
check("Mosquito" in body and ">-1<" in body and ">+1<" in body and "v1-test" in body, "real rows, signed differences, version")
check("Controlled inference (development provider)" in body and str(run1) in body, "provider clearly labelled + run id")
check("Shortage" in body and "Surplus" in body and "Unidentified" in body and "Match" in body, "result badges")
check(f"/media/{capture_id}" in body and "91.0%" in body, "capture used + confidence shown")
check("Proceed to Validation" in body and 'name="inference_run_id"' not in body, "no second run form; continue button")
check(client.get(f"/operator/sessions/{ws}/validation").status_code == 200, "Human Validation receives real UUID")

print("== 9-11, 28. surplus / exact / low confidence / snapshot")
ws2 = new_session()
with flask_app.app_context():
    item = db.session.execute(select(KitItem).where(KitItem.kit_id == I["kit"], KitItem.family_id == I["mosq"])).scalar_one()
    item.quantity = 20; db.session.commit()  # master kit changed after the session snapshot
r, _ = infer(ws2, [(0, 9, 0.95), (1, 6, 0.50), (2, 2, 0.90)])
codes2 = {(d.family_id, code) for d, code in discrepancies_of(ws2)}
check((I["mosq"], "surplus") in codes2 and {e.family_id: e for e in events_of(ws2)}[I["mosq"]].expected_quantity == 8,
      "8 vs 9 -> surplus, expected from snapshot (not KitItem=20)")
check((I["kelly"], "low_confidence") in codes2 and (I["kelly"], "shortage") not in codes2 and (I["kelly"], "surplus") not in codes2,
      "confidence 0.50 < 0.70 -> low_confidence only")
check(not any(f == I["allis"] for f, _ in codes2), "2 vs 2 -> no discrepancy")
ws3 = new_session()  # snapshot now has Mosquito 20
r, _ = infer(ws3, [(0, 8, 0.99), (1, 6, 0.99), (2, 2, 0.99)])
check((I["mosq"], "shortage") in {(d.family_id, c) for d, c in discrepancies_of(ws3)}, "new session uses new snapshot (20 vs 8)")
with flask_app.app_context():
    item = db.session.execute(select(KitItem).where(KitItem.kit_id == I["kit"], KitItem.family_id == I["mosq"])).scalar_one()
    item.quantity = 8; db.session.commit()
ws4 = new_session()
r, _ = infer(ws4, [(0, 8, 0.72), (1, 6, 0.99), (2, 2, 0.99)])
check(discrepancies_of(ws4) == [], "8 vs 8 above threshold -> no discrepancy")

print("== 26. other institution")
CURRENT["user"] = fake_user(I["op_b"], I["b"], "operator_cde", "OpB")
r, _ = infer(ws4, GOOD)
check(r.status_code == 404 and client.get(f"/operator/sessions/{ws}/ai-detection").status_code == 404, "foreign session -> 404")
CURRENT["user"] = fake_user(I["op_user"], I["a"], "operator_cde", "Op")

print("== empty state + template")
ws5 = new_session()
body = client.get(f"/operator/sessions/{ws5}/ai-detection").get_data(as_text=True)
check('name="inference_run_id"' in body and "Analysis pending" in body and "Mosquito" in body, "controlled form with mapped classes")
tpl = open(os.path.join(APP, "templates", "operator", "sessions", "ai_detection.html"), encoding="utf-8").read()
check(not any(s in tpl for s in ("TEMPORARY DEMO DATA", "WS-026", "v0.4.2", "94", "Kelly Clamp")), "template free of demo results")

shutil.rmtree(STORE, ignore_errors=True)
finish()
