"""WorkSession creation + ExpectedInventory snapshot + scoping + /verify + admin scoping tests."""
import os
import re
import uuid

from _common import HERE, check, fake_user, finish, setup_app

flask_app, db = setup_app("smoke_sessions.db")

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

import controllers.routes as routes  # noqa: E402
import services.session_service as session_service  # noqa: E402
from controllers.view_data import admin_dashboard_overview  # noqa: E402
from services.analytics_service import audit_entries  # noqa: E402
from models.AccessAudit import AccessAudit  # noqa: E402
from models.CaptureStation import CaptureStation  # noqa: E402
from models.CatInstrumentCategory import CatInstrumentCategory  # noqa: E402
from models.CatOperationPhase import CatOperationPhase  # noqa: E402
from models.CatOperationStatus import CatOperationStatus  # noqa: E402
from models.CatProcedureType import CatProcedureType  # noqa: E402
from models.CatSessionStatus import CatSessionStatus  # noqa: E402
from models.ExpectedInventory import ExpectedInventory  # noqa: E402
from models.Institution import Institution  # noqa: E402
from models.InstrumentFamily import InstrumentFamily  # noqa: E402
from models.Kit import Kit  # noqa: E402
from models.KitItem import KitItem  # noqa: E402
from models.OperatingRoom import OperatingRoom  # noqa: E402
from models.Operation import Operation  # noqa: E402
from models.ProcedureKit import ProcedureKit  # noqa: E402
from models.ProcedurePhase import ProcedurePhase  # noqa: E402
from models.Role import Role  # noqa: E402
from models.User import User  # noqa: E402
from models.UserRole import UserRole  # noqa: E402
from models.WorkSession import WorkSession  # noqa: E402

I = {}
with flask_app.app_context():
    a, b = Institution(name="Hospital A"), Institution(name="Hospital B")
    db.session.add_all([a, b]); db.session.flush()
    open_status = CatSessionStatus(code="open", name="Open")
    scheduled = CatOperationStatus(code="scheduled", name="Scheduled")
    cat = CatInstrumentCategory(code="hemostasis", name="Hemostasis")
    pre = CatOperationPhase(code="pre_incision", name="Initial Count Before Incision"); final = CatOperationPhase(code="final_count", name="Final Count")
    proc = CatProcedureType(code="APPX", name="Appendectomy")
    op_role_a = Role(code="operator_cde", description="Operator", institution_id=a.id)
    adm_role_a = Role(code="it_admin", description="Admin", institution_id=a.id)
    op_role_b = Role(code="operator_cde", description="Operator B", institution_id=b.id)
    db.session.add_all([open_status, scheduled, cat, pre, final, proc, op_role_a, adm_role_a, op_role_b]); db.session.flush()
    operator = User(name="Oper A", email="op@a.org", password_hash="x", institution_id=a.id)
    other_op = User(name="Oper A2", email="op2@a.org", password_hash="x", institution_id=a.id)
    admin = User(name="Admin A", email="adm@a.org", password_hash="x", institution_id=a.id)
    op_b = User(name="Oper B", email="op@b.org", password_hash="x", institution_id=b.id)
    kelly = InstrumentFamily(code="KEL", name="Kelly", category_id=cat.id)
    mosq = InstrumentFamily(code="MOS", name="Mosquito", category_id=cat.id)
    room_a = OperatingRoom(code="OR-A", name="Room A", institution_id=a.id)
    room_a2 = OperatingRoom(code="OR-A2", name="Room A2", institution_id=a.id)
    room_b = OperatingRoom(code="OR-B", name="Room B", institution_id=b.id)
    db.session.add_all([operator, other_op, admin, op_b, kelly, mosq, room_a, room_a2, room_b]); db.session.flush()
    db.session.add_all([UserRole(user_id=operator.id, role_id=op_role_a.id), UserRole(user_id=admin.id, role_id=adm_role_a.id)])
    kit = Kit(name="General Surgery", institution_id=a.id)
    kit_unlinked = Kit(name="Unlinked Kit", institution_id=a.id)
    kit_empty = Kit(name="Empty Kit", institution_id=a.id)
    kit_b = Kit(name="Kit B", institution_id=b.id)
    st_a = CaptureStation(name="CDE-A", room_id=room_a.id)
    st_a2 = CaptureStation(name="CDE-A2", room_id=room_a2.id)
    st_b = CaptureStation(name="CDE-B", room_id=room_b.id)
    db.session.add_all([kit, kit_unlinked, kit_empty, kit_b, st_a, st_a2, st_b]); db.session.flush()
    db.session.add_all([
        KitItem(kit_id=kit.id, family_id=kelly.id, quantity=6), KitItem(kit_id=kit.id, family_id=mosq.id, quantity=8),
        KitItem(kit_id=kit_unlinked.id, family_id=kelly.id, quantity=1), KitItem(kit_id=kit_b.id, family_id=kelly.id, quantity=3),
        ProcedureKit(procedure_type_id=proc.id, kit_id=kit.id, is_default=True),
        ProcedureKit(procedure_type_id=proc.id, kit_id=kit_empty.id),
        ProcedureKit(procedure_type_id=proc.id, kit_id=kit_b.id),
        ProcedurePhase(procedure_type_id=proc.id, phase_id=pre.id, sort_order=1),
    ])
    op_a = Operation(status_id=scheduled.id, procedure_type_id=proc.id, room_id=room_a.id, institution_id=a.id)
    op_b_row = Operation(status_id=scheduled.id, procedure_type_id=proc.id, room_id=room_b.id, institution_id=b.id)
    db.session.add_all([op_a, op_b_row]); db.session.flush()
    ws_b = WorkSession(status_id=open_status.id, user_id=op_b.id, operation_id=op_b_row.id, station_id=st_b.id, kit_id=kit_b.id)
    db.session.add(ws_b)
    db.session.add(AccessAudit(actor_type="user", actor_user_id=op_b.id, action="SECRET_B_EVENT", resource_type="work_session",
                               resource_id=uuid.uuid4(), institution_id=b.id))
    db.session.commit()
    I.update(a=a.id, b=b.id, operator=operator.id, other_op=other_op.id, admin=admin.id, kit=kit.id, kit_unlinked=kit_unlinked.id,
             kit_empty=kit_empty.id, kit_b=kit_b.id, st_a=st_a.id, st_a2=st_a2.id, st_b=st_b.id, op_a=op_a.id, op_b=op_b_row.id,
             pre=pre.id, final=final.id, ws_b=ws_b.id, kelly=kelly.id)

CURRENT = {"user": fake_user(I["operator"], I["a"], "operator_cde", "Oper A")}
routes._current_user = lambda: CURRENT["user"]
client = flask_app.test_client()


def count(model, **filters):
    with flask_app.app_context():
        return db.session.scalar(select(func.count()).select_from(model).filter_by(**filters))


def valid(**overrides):
    data = {"operation_id": str(I["op_a"]), "kit_id": str(I["kit"]), "capture_station_id": str(I["st_a"]),
            "counting_phase": str(I["pre"]), "instrument_readiness_confirmed": "on"}
    data.update(overrides)
    return data


print("== 1. GET New Session")
page = client.get("/operator/sessions/new").get_data(as_text=True)
check("General Surgery" in page and "CDE-A<" in page.replace("CDE-A </", "CDE-A<"), "real kit + station options")
check("Unlinked Kit" not in page and "Kit B" not in page, "kit not linked to procedure / other institution hidden")
check("CDE-A2" not in page and "CDE-B" not in page, "station of other room / institution hidden")
check("Kelly" in page and ">6<" in page, "expected inventory preview from kit")
check("WS-026" not in page, "no demo session id")
check(client.get(f"/operator/sessions/new?operation_id={I['op_b']}").status_code == 200, "foreign operation id in query ignored")

print("== 2-5, 10, 12. Valid POST")
before = count(WorkSession)
resp = client.post("/operator/sessions/new", data=valid(user_id=str(I["other_op"]), operator_id=str(I["other_op"])))
check(resp.status_code == 302, f"POST valid -> {resp.status_code}")
match = re.search(r"/operator/sessions/([0-9a-f-]{36})/capture$", resp.headers.get("Location", ""))
check(match is not None, "redirects to real UUID capture URL")
check(count(WorkSession) == before + 1, "exactly one WorkSession created")
ws_id = uuid.UUID(match.group(1))
with flask_app.app_context():
    ws = db.session.get(WorkSession, ws_id)
    status_code = db.session.get(CatSessionStatus, ws.status_id).code
    snap = db.session.execute(select(ExpectedInventory).where(ExpectedInventory.session_id == ws_id)).scalars().all()
    check(ws.user_id == I["operator"], "user_id = authenticated operator (form user_id ignored)")
    check(status_code == "open" and ws.ended_at is None and ws.closed_by_user_id is None, "status open, not closed")
    check(ws.current_phase_id == I["pre"] and ws.phase_changed_at is not None, "phase + phase_changed_at set")
    check(len(snap) == 2 and {e.source for e in snap} == {"kit_snapshot"}, "2 ExpectedInventory rows with source kit_snapshot")
    check({(e.family_id, e.expected_quantity) for e in snap} == {(I["kelly"], 6), (next(e.family_id for e in snap if e.family_id != I["kelly"]), 8)}, "quantities copied")
    audit = db.session.execute(select(AccessAudit).where(AccessAudit.action == "CREATE_SESSION")).scalars().all()
    check(len(audit) == 1 and audit[0].resource_id == ws_id and audit[0].institution_id == I["a"]
          and audit[0].actor_user_id == I["operator"] and audit[0].outcome == "success", "CREATE_SESSION audited")

print("== 6. Kit master change does not alter snapshot")
with flask_app.app_context():
    item = db.session.execute(select(KitItem).where(KitItem.kit_id == I["kit"], KitItem.family_id == I["kelly"])).scalar_one()
    item.quantity = 8
    db.session.commit()
    kel = db.session.execute(select(ExpectedInventory).where(ExpectedInventory.session_id == ws_id, ExpectedInventory.family_id == I["kelly"])).scalar_one()
    check(kel.expected_quantity == 6, "ExpectedInventory Kelly still 6 after kit changed to 8")
cap = client.get(f"/operator/sessions/{ws_id}/capture")
body = cap.get_data(as_text=True)
check(cap.status_code == 200 and "Kelly" in body and ">6<" in body and ">8<" in body, "capture shows frozen snapshot (6 and 8)")
check("General Surgery" in body and "CDE-A" in body and "Initial Count Before Incision" in body and "Appendectomy" in body, "capture shows real context")
check("WS-026" not in body and "No capture yet" in body, "no demo id; real empty capture state")

print("== 7-9. Rejections (no session created)")
n = count(WorkSession)
check(client.post("/operator/sessions/new", data=valid(kit_id=str(I["kit_empty"]))).status_code == 409, "empty kit -> 409")
check(client.post("/operator/sessions/new", data=valid(kit_id=str(I["kit_unlinked"]))).status_code == 400, "kit not linked to procedure -> 400")
check(client.post("/operator/sessions/new", data=valid(kit_id=str(I["kit_b"]))).status_code == 400, "kit of other institution -> 400")
check(client.post("/operator/sessions/new", data=valid(capture_station_id=str(I["st_b"]))).status_code == 400, "station other institution -> 400")
check(client.post("/operator/sessions/new", data=valid(capture_station_id=str(I["st_a2"]))).status_code == 400, "station in another room -> 400")
check(client.post("/operator/sessions/new", data=valid(operation_id=str(I["op_b"]))).status_code == 400, "operation other institution -> 400")
check(client.post("/operator/sessions/new", data=valid(counting_phase=str(I["final"]))).status_code == 400, "phase not configured for procedure -> 400")
check(client.post("/operator/sessions/new", data=valid(instrument_readiness_confirmed="")).status_code == 400, "readiness unchecked -> 400")
check(client.post("/operator/sessions/new", data=valid(kit_id="not-a-uuid")).status_code == 400, "garbage id -> 400")
check(count(WorkSession) == n and count(ExpectedInventory) == 2 + 0, "no WorkSession / ExpectedInventory left behind")

print("== 11. Failure during snapshot rolls back")
original = session_service.snapshot_expected_inventory


def failing_snapshot(work_session, items):
    from extensions import db as _db
    _db.session.add(ExpectedInventory(session_id=work_session.id, family_id=items[0].family_id,
                                      expected_quantity=items[0].quantity, source="kit_snapshot"))
    _db.session.flush()
    raise SQLAlchemyError("simulated failure on second row")


session_service.snapshot_expected_inventory = failing_snapshot
resp = client.post("/operator/sessions/new", data=valid())
session_service.snapshot_expected_inventory = original
check(resp.status_code == 503, f"snapshot failure -> {resp.status_code} (controlled)")
check(count(WorkSession) == n and count(ExpectedInventory) == 2 and count(AccessAudit, action="CREATE_SESSION") == 1,
      "WorkSession, partial snapshot and audit rolled back")

print("== initial status missing -> 409")
with flask_app.app_context():
    st = db.session.execute(select(CatSessionStatus).where(CatSessionStatus.code == "open")).scalar_one()
    st.code = "renamed"; db.session.commit()
check(client.post("/operator/sessions/new", data=valid()).status_code == 409, "missing 'open' status -> 409")
with flask_app.app_context():
    st = db.session.execute(select(CatSessionStatus).where(CatSessionStatus.code == "renamed")).scalar_one()
    st.code = "open"; db.session.commit()

print("== 13-14. Session URL scoping")
check(client.get(f"/operator/sessions/{uuid.uuid4()}/capture").status_code == 404, "unknown UUID -> 404")
check(client.get("/operator/sessions/WS-026/capture").status_code == 404, "demo id WS-026 -> 404")
check(client.get(f"/operator/sessions/{I['ws_b']}/capture").status_code == 404, "other institution session -> 404")
check(count(AccessAudit, action="ACCESS_WORK_SESSION", outcome="denied") == 1, "cross-institution access audited as denied")
for stage in ("ai-detection", "validation", "validation-summary", "discrepancy", "awaiting-review", "correction", "ready-to-close"):
    r = client.get(f"/operator/sessions/{ws_id}/{stage}")
    if stage == "validation-summary":  # blocked until human validation exists
        check(r.status_code == 302 and r.headers["Location"].endswith(f"/operator/sessions/{ws_id}/validation"), "summary redirects to validation")
    else:
        check(r.status_code == 200 and str(ws_id) in r.get_data(as_text=True), f"stage {stage} renders real session id")
    check(client.get(f"/operator/sessions/{I['ws_b']}/{stage}").status_code == 404, f"stage {stage} foreign -> 404")
check(client.get(f"/operator/sessions/closed/{ws_id}").status_code == 200, "closed details real session 200")
check(client.get(f"/operator/sessions/closed/{I['ws_b']}").status_code == 404, "closed details foreign -> 404")

print("== 15. Lists")
short = str(ws_id)[:8].upper()
for url in ("/operator/sessions", "/operator/sessions/history", "/operator/dashboard"):
    body = client.get(url).get_data(as_text=True)
    check(short in body, f"{url} shows new session")
    check(str(I["ws_b"])[:8].upper() not in body, f"{url} hides other institution session")
check(f"/operator/sessions/{ws_id}/capture" in client.get("/operator/sessions").get_data(as_text=True), "list links to real capture URL")

print("== 17. Admin dashboard / audit log scoping")
CURRENT["user"] = fake_user(I["admin"], I["a"], "it_admin", "Admin A")
log = client.get("/admin/audit-log")
body = log.get_data(as_text=True)
check(log.status_code == 200 and "CREATE_SESSION" in body and "SECRET_B_EVENT" not in body, "audit log only own institution")
check("Daniel Brooks" not in body, "audit log demo rows removed")
check(client.get("/admin/dashboard").status_code == 200, "admin dashboard renders")
with flask_app.app_context():
    overview = admin_dashboard_overview(I["a"])
    system = {item["label"]: item["value"] for item in overview["system_overview"]}
    check(system["Configured Roles"] == 2 and system["Operating Rooms"] == 2 and system["Capture Stations"] == 2,
          f"dashboard counts scoped to A: {system}")
    check(system["Active Institution"] == 1, "only own institution counted")
    check(all(entry["event_label"] != "SECRET_B_EVENT" for entry in audit_entries(I["a"])), "audit entries scoped")

print("== strict ProcedurePhase")
with flask_app.app_context():
    sched_id = db.session.execute(select(CatOperationStatus.id).where(CatOperationStatus.code == "scheduled")).scalar_one()
    proc2 = CatProcedureType(code="NOPHASE", name="Procedure Without Phases")
    db.session.add(proc2); db.session.flush()
    db.session.add(ProcedureKit(procedure_type_id=proc2.id, kit_id=I["kit"]))
    op_nophase = Operation(status_id=sched_id, procedure_type_id=proc2.id, room_id=db.session.get(CaptureStation, I["st_a"]).room_id,
                           institution_id=I["a"])
    db.session.add(op_nophase); db.session.commit()
    I["op_nophase"], I["proc2"] = op_nophase.id, proc2.id
CURRENT["user"] = fake_user(I["operator"], I["a"], "operator_cde", "Oper A")
page = client.get(f"/operator/sessions/new?operation_id={I['op_nophase']}").get_data(as_text=True)
check("No active counting phase is configured" in page and "Initial Count Before Incision" not in page and "Final Count" not in page,
      "GET: no ProcedurePhase -> clear notice, no global phases offered")
n = count(WorkSession)
r = client.post("/operator/sessions/new", data=valid(operation_id=str(I["op_nophase"]), counting_phase=str(I["pre"])))
check(r.status_code == 409 and count(WorkSession) == n, "POST without ProcedurePhase -> 409, no WorkSession")
with flask_app.app_context():
    db.session.add(ProcedurePhase(procedure_type_id=I["proc2"], phase_id=I["final"], sort_order=1, active=False)); db.session.commit()
r = client.post("/operator/sessions/new", data=valid(operation_id=str(I["op_nophase"]), counting_phase=str(I["final"])))
check(r.status_code == 409 and count(WorkSession) == n, "only inactive ProcedurePhase -> 409")
with flask_app.app_context():
    pp = db.session.execute(select(ProcedurePhase).where(ProcedurePhase.phase_id == I["pre"])).scalar_one()
    pp.active = False; db.session.commit()
body = client.get(f"/operator/sessions/{ws_id}/capture").get_data(as_text=True)
with flask_app.app_context():
    kept = db.session.get(WorkSession, ws_id).current_phase_id
check(kept == I["pre"] and "Initial Count Before Incision" in body, "existing session keeps its current_phase_id after phase deactivation")
with flask_app.app_context():
    pp = db.session.execute(select(ProcedurePhase).where(ProcedurePhase.phase_id == I["pre"])).scalar_one()
    pp.active = True; db.session.commit()

# BackendAuthService /verify checks live in apps/BackendAuthService/tests/scenarios/auth_audit.py
finish()
