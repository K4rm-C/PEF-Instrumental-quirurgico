"""Smoke tests for the admin CRUD against a throwaway SQLite DB (PG-only DDL adapted here only)."""
import os
import re
import sys
import uuid
import warnings
import sys as _sys
_sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from pathlib import Path
APP = str(Path(__file__).resolve().parents[2])
import tempfile as _tempfile
HERE = os.environ.get('PEF_TEST_WORKDIR') or _tempfile.mkdtemp(prefix='pef-test-')
DB_FILE = os.path.join(HERE, "smoke.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)
sys.path.insert(0, APP)
os.chdir(APP)
warnings.simplefilter("ignore", DeprecationWarning)

import config  # noqa: E402
config.Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + DB_FILE
config.Config.SQLALCHEMY_ENGINE_OPTIONS = {}

from sqlalchemy import event, select, func, text  # noqa: E402
from sqlalchemy.dialects.postgresql import INET, JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb(element, compiler, **kw):
    return "JSON"


@compiles(INET, "sqlite")
def _inet(element, compiler, **kw):
    return "TEXT"


import app as app_module  # noqa: E402
import controllers.routes as routes  # noqa: E402
from extensions import db  # noqa: E402
from sqlalchemy.orm import configure_mappers  # noqa: E402
import models  # noqa: E402,F401
from models.AccessAudit import AccessAudit  # noqa: E402
from models.CatInstrumentCategory import CatInstrumentCategory  # noqa: E402
from models.CatInstrumentCycleStatus import CatInstrumentCycleStatus  # noqa: E402
from models.CatOperationPhase import CatOperationPhase  # noqa: E402
from models.CaptureStation import CaptureStation  # noqa: E402
from models.CatProcedureType import CatProcedureType  # noqa: E402
from models.Institution import Institution  # noqa: E402
from models.Instrument import Instrument  # noqa: E402
from models.InstrumentFamily import InstrumentFamily  # noqa: E402
from models.Kit import Kit  # noqa: E402
from models.KitItem import KitItem  # noqa: E402
from models.OperatingRoom import OperatingRoom  # noqa: E402
from models.ProcedureKit import ProcedureKit  # noqa: E402
from models.ProcedurePhase import ProcedurePhase  # noqa: E402
from models.Role import Role  # noqa: E402
from models.User import User  # noqa: E402
from models.UserRole import UserRole  # noqa: E402
from models.YoloModel import YoloModel  # noqa: E402
from werkzeug.security import check_password_hash  # noqa: E402

flask_app = app_module.app
flask_app.config["TESTING"] = True
FAILS = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


def adapt_schema():
    """SQLite stand-ins for PG defaults; partial indexes keep their WHERE via sqlite_where."""
    for table in db.metadata.tables.values():
        for column in table.columns:
            sd = column.server_default
            if sd is None:
                continue
            txt = str(getattr(sd.arg, "text", sd.arg))
            if "gen_random_uuid" in txt:
                column.server_default = None
            elif "now()" in txt:
                column.server_default = type(sd)(text("CURRENT_TIMESTAMP"))
            elif "::jsonb" in txt:
                column.server_default = type(sd)(text(txt.replace("::jsonb", "")))
        for index in table.indexes:
            where = index.dialect_options["postgresql"].get("where")
            if where is not None:
                index.dialect_options["sqlite"]["where"] = where


with flask_app.app_context():
    configure_mappers()
    adapt_schema()
    engine = db.engine
    def _on_connect(con, rec):
        con.execute("PRAGMA foreign_keys=ON")
        con.create_function("jsonb_typeof", 1, lambda v: "object" if v and str(v).lstrip().startswith("{") else "other")
    event.listen(engine, "connect", _on_connect)
    engine.dispose()
    db.create_all()

    inst_a = Institution(name="Hospital A"); inst_b = Institution(name="Hospital B")
    db.session.add_all([inst_a, inst_b]); db.session.flush()
    admin_role = Role(code="it_admin", description="Administrator", institution_id=inst_a.id)
    op_role = Role(code="operator_cde", description="Operator", institution_id=inst_a.id)
    b_role = Role(code="operator_cde", description="Operator B", institution_id=inst_b.id)
    admin = User(name="Admin", email="admin@a.org", password_hash="x", institution_id=inst_a.id)
    cat = CatInstrumentCategory(code="hemostasis", name="Hemostasis")
    available = CatInstrumentCycleStatus(code="available", name="Available")
    sterile = CatInstrumentCycleStatus(code="sterilization", name="Sterilization")
    pre = CatOperationPhase(code="pre", name="Pre-Procedure"); post = CatOperationPhase(code="post", name="Post-Procedure")
    db.session.add_all([admin_role, op_role, b_role, admin, cat, available, sterile, pre, post]); db.session.flush()
    db.session.add(UserRole(user_id=admin.id, role_id=admin_role.id))
    b_room = OperatingRoom(code="B-OR", name="B room", institution_id=inst_b.id)
    db.session.add(b_room); db.session.flush()
    b_family = InstrumentFamily(code="B-FAM", name="B family", category_id=cat.id)
    db.session.add(b_family); db.session.flush()
    b_instr = Instrument(internal_code="B-1", family_id=b_family.id, cycle_status_id=available.id, institution_id=inst_b.id)
    db.session.add(b_instr); db.session.commit()
    ids = dict(inst_a=inst_a.id, admin=admin.id, cat=cat.id, available=available.id, sterile=sterile.id,
               pre=pre.id, post=post.id, b_instr=b_instr.id, b_room=b_room.id, b_family=b_family.id)

FAKE_ADMIN = {"id": str(ids["admin"]), "name": "Admin", "email": "admin@a.org", "institution_id": str(ids["inst_a"]),
              "roles": [{"code": "it_admin"}], "role_code": "it_admin", "role_label": "Administrator",
              "profile_role_label": "Administrator", "avatar_url": None}
routes._current_user = lambda: FAKE_ADMIN
client = flask_app.test_client()


def post(url, data, expect=302):
    response = client.post(url, data=data)
    check(response.status_code == expect, f"POST {url} -> {response.status_code} (expected {expect})")
    return response


def one(model, **filters):
    with flask_app.app_context():
        return db.session.execute(select(model).filter_by(**filters)).scalar_one_or_none()


def count(model, **filters):
    with flask_app.app_context():
        return db.session.scalar(select(func.count()).select_from(model).filter_by(**filters))


print("== GET pages")
admin_pages = [r.rule for r in flask_app.url_map.iter_rules()
               if r.rule.startswith("/admin") and "<" not in r.rule and "GET" in r.methods]
for url in admin_pages:
    response = client.get(url)
    body = response.get_data(as_text=True)
    check(response.status_code == 200, f"GET {url} -> {response.status_code}")
    check(not re.search(r"Kelly Clamp|Delivery Kit|Alex Morgan|Station CDE-01|v0\.4\.2|Appendectomy|Hospital Institution", body)
          or url in ("/admin/dashboard", "/admin/audit-log"), f"no demo data on {url}")

print("== Instrument families")
post("/admin/instrument-families/new", {"code": "FAM-1", "name": "Kelly", "category": str(ids["cat"]), "status": "active"})
post("/admin/instrument-families/new", {"code": "FAM-2", "name": "Mosquito", "category": str(ids["cat"]), "status": "active"})
dup = post("/admin/instrument-families/new", {"code": "FAM-1", "name": "Dup", "category": str(ids["cat"])}, expect=400)
check("already exists" in dup.get_data(as_text=True) and 'value="Dup"' in dup.get_data(as_text=True), "error flashed and input kept")
post("/admin/instrument-families/new", {"code": "FAM-9", "name": "", "category": "nope"}, expect=400)
fam1, fam2 = one(InstrumentFamily, code="FAM-1"), one(InstrumentFamily, code="FAM-2")
post(f"/admin/instrument-families/{fam2.id}/edit", {"name": "Mosquito curved", "category": str(ids["cat"]), "status": "inactive"})
check(one(InstrumentFamily, code="FAM-2").active is False, "family deactivated (not deleted)")
post(f"/admin/instrument-families/{fam2.id}/edit", {"name": "Mosquito curved", "category": str(ids["cat"]), "status": "active"})
check(one(InstrumentFamily, code="FAM-2").active is True, "family reactivated")

print("== Instruments")
post("/admin/instruments/new", {"internal_code": "INS-1", "instrument_family": str(fam1.id), "cycle_status": str(ids["available"]), "active_status": "active"})
post("/admin/instruments/new", {"internal_code": "INS-1", "instrument_family": str(fam1.id), "cycle_status": str(ids["available"])}, expect=400)
post("/admin/instruments/new", {"internal_code": "INS-2", "instrument_family": str(uuid.uuid4()), "cycle_status": str(ids["available"])}, expect=400)
ins = one(Instrument, internal_code="INS-1")
check(ins.institution_id == ids["inst_a"], "instrument created in admin's institution")
post(f"/admin/instruments/{ins.id}/edit", {"instrument_family": str(fam1.id), "cycle_status": str(ids["sterile"]), "active_status": "inactive"})
check(one(Instrument, internal_code="INS-1").cycle_status_id == ids["sterile"], "instrument updated")
check(client.get(f"/admin/instruments/{ids['b_instr']}/edit").status_code == 404, "other institution instrument -> 404")
check(client.post(f"/admin/instruments/{ids['b_instr']}/edit", data={"active_status": "inactive"}).status_code == 404, "cross-institution POST -> 404")
check(count(AccessAudit, action="ACCESS_INSTRUMENT", outcome="denied") >= 1, "denied access audited")
check(client.get("/admin/instruments/not-a-uuid/edit").status_code == 404, "bad uuid -> 404")

print("== Kits")
kit_data = {"name": "General Surgery", "status": "active",
            "instrument_family[]": [str(fam1.id), str(fam2.id)], "expected_quantity[]": ["6", "8"]}
post("/admin/kits/new", kit_data)
kit = one(Kit, name="General Surgery")
check(count(KitItem, kit_id=kit.id) == 2 and kit.version == 1, "kit + 2 items saved")
post("/admin/kits/new", {"name": "Broken", "instrument_family[]": [str(fam1.id), str(fam1.id)], "expected_quantity[]": ["1", "2"]}, expect=400)
post("/admin/kits/new", {"name": "Broken2", "instrument_family[]": [str(fam1.id)], "expected_quantity[]": ["0"]}, expect=400)
check(one(Kit, name="Broken") is None and one(Kit, name="Broken2") is None, "failed kits leave no partial rows")
post(f"/admin/kits/{kit.id}/edit", {"name": "General Surgery", "status": "active", "instrument_family[]": [str(fam1.id)], "expected_quantity[]": ["4"]})
with flask_app.app_context():
    items = db.session.execute(select(KitItem).where(KitItem.kit_id == kit.id)).scalars().all()
check(len(items) == 1 and items[0].quantity == 4, "kit item updated and removed family deleted")
page = client.get(f"/admin/kits/{kit.id}/edit").get_data(as_text=True)
check('value="4"' in page and "Kelly" in page, "edit kit shows real composition")

print("== Procedures")
kit2_data = dict(kit_data, name="Alt kit")
post("/admin/kits/new", kit2_data)
kit2 = one(Kit, name="Alt kit")
proc = {"code": "APPX", "name": "Appendectomy real",
        "kit[]": [str(kit.id), str(kit2.id)], "technique_label[]": ["Std", "Alt"], "is_default[]": ["0"], "kit_active[]": ["0", "1"],
        "phase[]": [str(ids["pre"]), str(ids["post"])], "sort_order[]": ["1", "2"], "count_required[]": ["0", "1"], "phase_active[]": ["0", "1"]}
post("/admin/procedures/new", proc)
pt = one(CatProcedureType, code="APPX")
check(count(ProcedureKit, procedure_type_id=pt.id) == 2 and one(ProcedureKit, procedure_type_id=pt.id, is_default=True).kit_id == kit.id, "procedure kits + default")
check(count(ProcedurePhase, procedure_type_id=pt.id) == 2, "procedure phases saved")
# swap default, swap phase order, drop second kit
edit = dict(proc, **{"kit[]": [str(kit2.id)], "technique_label[]": ["Alt"], "is_default[]": ["0"], "kit_active[]": ["0"],
                     "sort_order[]": ["2", "1"]})
post(f"/admin/procedures/{pt.id}/edit", edit)
check(one(ProcedureKit, procedure_type_id=pt.id, kit_id=kit.id).active is False, "removed kit association deactivated")
check(one(ProcedureKit, procedure_type_id=pt.id, is_default=True).kit_id == kit2.id, "default switched")
check(one(ProcedurePhase, procedure_type_id=pt.id, phase_id=ids["pre"]).sort_order == 2, "phase order swapped")
post(f"/admin/procedures/{pt.id}/edit", dict(proc, **{"is_default[]": ["0", "1"]}), expect=400)
post(f"/admin/procedures/{pt.id}/edit", dict(proc, **{"sort_order[]": ["1", "1"]}), expect=400)

print("== Operating rooms / stations")
post("/admin/configuration/operating-rooms/new", {"code": "OR-1", "name": "Room 1", "status": "active"})
post("/admin/configuration/operating-rooms/new", {"code": "OR-1", "name": "Dup"}, expect=400)
room = one(OperatingRoom, code="OR-1", institution_id=ids["inst_a"])
post("/admin/configuration/capture-stations/new", {"name": "CDE-A", "operating_room": str(room.id), "status": "active"})
post("/admin/configuration/capture-stations/new", {"name": "Bad", "operating_room": str(ids["b_room"])}, expect=400)
station = one(CaptureStation, name="CDE-A")
post(f"/admin/configuration/capture-stations/{station.id}/edit", {"name": "CDE-A", "operating_room": str(room.id), "status": "inactive"})
check(one(CaptureStation, name="CDE-A").active is False and one(CaptureStation, name="CDE-A").roi is None, "station deactivated, ROI untouched")
post(f"/admin/configuration/operating-rooms/{room.id}/edit", {"code": "OR-1", "name": "Room One", "status": "active"})
check("Room One" in client.get("/admin/configuration").get_data(as_text=True), "configuration lists real room")

print("== Users / roles")
post("/admin/roles/new", {"code": "supervisor_quality", "description": "Supervisor"})
post("/admin/roles/new", {"code": "supervisor_quality", "description": "dup"}, expect=400)
sup = one(Role, code="supervisor_quality")
post(f"/admin/roles/{sup.id}/edit", {"description": "Quality supervisor"})
check(one(Role, code="supervisor_quality").description == "Quality supervisor", "role updated")
user_form = {"name": "Nurse", "email": "nurse@a.org", "institution": str(uuid.uuid4()), "roles[]": ["operator_cde", "supervisor_quality"],
             "status": "active", "password": "secret123", "confirm_password": "secret123"}
post("/admin/users/new", user_form)
nurse = one(User, email="nurse@a.org")
check(nurse.institution_id == ids["inst_a"], "user institution from token, not from form")
check(check_password_hash(nurse.password_hash, "secret123") and nurse.password_hash != "secret123", "password hashed")
check(count(UserRole, user_id=nurse.id) == 2, "two roles assigned")
post("/admin/users/new", dict(user_form, email="x@a.org", password="short", confirm_password="short"), expect=400)
post("/admin/users/new", dict(user_form, email="y@a.org", **{"roles[]": ["operator_cde_missing"]}), expect=400)
post(f"/admin/users/{nurse.id}/edit", {"name": "Nurse 2", "email": "nurse@a.org", "roles[]": ["operator_cde"], "status": "active"})
check(count(UserRole, user_id=nurse.id) == 1, "removed role deleted from UserRole")
post(f"/admin/users/{nurse.id}/password", {"new_password": "another123", "confirm_new_password": "another123"})
check(check_password_hash(one(User, email="nurse@a.org").password_hash, "another123"), "password changed")
post(f"/admin/users/{nurse.id}/password", {"new_password": "a", "confirm_new_password": "a"}, expect=302)
post(f"/admin/users/{nurse.id}/deactivate", {})
check(one(User, email="nurse@a.org").active is False, "user deactivated")
post(f"/admin/users/{ids['admin']}/deactivate", {})
check(one(User, email="admin@a.org").active is True, "self-deactivation blocked")
check("nurse@a.org" in client.get("/admin/users").get_data(as_text=True), "users list real")

print("== Vision models")
post("/admin/vision-models/new", {"version_tag": "v1", "model_asset_reference": "gs://models/yolo/v1.pt", "checksum": "a" * 64,
                                  "active": "on", "yolo_class_id[]": ["0", "1"], "instrument_family[]": [str(fam1.id), str(fam2.id)]})
post("/admin/vision-models/new", {"version_tag": "v2", "model_asset_reference": "gs://models/yolo/v2.pt",
                                  "yolo_class_id[]": ["0"], "instrument_family[]": [str(fam1.id)]})
post("/admin/vision-models/new", {"version_tag": "v3", "model_asset_reference": "not-a-uri"}, expect=400)
v1, v2 = one(YoloModel, version_tag="v1"), one(YoloModel, version_tag="v2")
check(v1.active and not v2.active, "v1 active on create")
post(f"/admin/vision-models/{v2.id}/activate", {})
check(one(YoloModel, version_tag="v2").active and not one(YoloModel, version_tag="v1").active, "activation swaps single active model")
post(f"/admin/vision-models/{v2.id}/edit", {"model_asset_reference": "gs://models/yolo/v2b.pt", "checksum": "",
                                             "yolo_class_id[]": ["3"], "instrument_family[]": [str(fam2.id)]})
check(client.get(f"/admin/vision-models/{v2.id}/edit").status_code == 200, "vision edit page renders (asset set)")

print("== Institution")
post("/admin/configuration/institution/edit", {"name": "Hospital A Renamed", "status": "active"})
check(one(Institution, id=ids["inst_a"]).name == "Hospital A Renamed", "institution renamed")

print("== Audit trail")
with flask_app.app_context():
    actions = set(db.session.execute(select(AccessAudit.action)).scalars())
expected = {"CREATE_INSTRUMENT_FAMILY", "UPDATE_INSTRUMENT_FAMILY", "DEACTIVATE_INSTRUMENT_FAMILY", "ACTIVATE_INSTRUMENT_FAMILY",
            "CREATE_INSTRUMENT", "UPDATE_INSTRUMENT", "DEACTIVATE_INSTRUMENT", "CREATE_KIT", "UPDATE_KIT", "CREATE_PROCEDURE",
            "UPDATE_PROCEDURE", "CREATE_OPERATING_ROOM", "UPDATE_OPERATING_ROOM", "CREATE_CAPTURE_STATION", "UPDATE_CAPTURE_STATION",
            "DEACTIVATE_CAPTURE_STATION", "CREATE_USER", "UPDATE_USER", "CHANGE_USER_PASSWORD", "DEACTIVATE_USER", "CREATE_ROLE",
            "UPDATE_ROLE", "CREATE_YOLO_MODEL", "UPDATE_YOLO_MODEL", "ACTIVATE_YOLO_MODEL", "DEACTIVATE_YOLO_MODEL",
            "UPDATE_INSTITUTION", "ACCESS_INSTRUMENT"}
check(expected <= actions, f"audit actions present (missing: {sorted(expected - actions)})")
check(count(AccessAudit, action="CREATE_KIT") == 2, "failed kit creations produced no audit rows")

print("== Every GET page again with real data")
for url in admin_pages + [f"/admin/kits/{kit.id}/edit", f"/admin/procedures/{pt.id}/edit", f"/admin/users/{nurse.id}/edit",
                          f"/admin/roles/{sup.id}/edit", f"/admin/vision-models/{v1.id}/edit",
                          f"/admin/instrument-families/{fam1.id}/edit", f"/admin/instruments/{ins.id}/edit",
                          f"/admin/configuration/operating-rooms/{room.id}/edit", f"/admin/configuration/capture-stations/{station.id}/edit"]:
    check(client.get(url).status_code == 200, f"GET {url}")

print(f"\n{'ALL PASSED' if not FAILS else f'{len(FAILS)} FAILED'}")
sys.exit(1 if FAILS else 0)
