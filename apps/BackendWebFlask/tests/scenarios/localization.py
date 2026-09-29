"""English / Spanish UI localization: locale resolution, the language switch, code-based labels.

Covers: default EN; anonymous switch (web session); strict validation (no persistence of invalid
values, no open redirect); authenticated switch persisted in user.ui_preferences.locale without
touching other keys; the choice surviving new requests / sign-out / sign-in; malformed
preferences falling back to EN; catalog values translated by code while user-created names,
renamed seeded rows and free text stay as stored; canonical English database values unchanged
after rendering Spanish pages; gettext catalog completeness and .mo/.po consistency.
"""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _common import APP, check, fake_user, finish, setup_app

flask_app, db = setup_app("smoke_localization.db")

from sqlalchemy import select  # noqa: E402

import controllers.routes as routes  # noqa: E402
import localization  # noqa: E402
from localization import labels  # noqa: E402
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
from models.ExpectedInventory import ExpectedInventory  # noqa: E402
from models.HumanCorrection import HumanCorrection  # noqa: E402
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

JUSTIFICATION = "Encontrada bajo el campo quirurgico (texto libre del operador)"
I = {}
with flask_app.app_context():
    inst = Institution(name="Hospital Norte")
    db.session.add(inst); db.session.flush()
    statuses = {code: CatSessionStatus(code=code, name=name) for code, name in (
        ("open", "Open"), ("counting", "Counting"), ("validating", "Validating"), ("closed", "Closed"))}
    scheduled = CatOperationStatus(code="scheduled", name="Scheduled")
    hemostasis = CatInstrumentCategory(code="hemostasis", name="Hemostasis")
    pre = CatOperationPhase(code="pre_incision", name="Initial Count Before Incision")
    appx = CatProcedureType(code="appendectomy", name="Appendectomy")
    custom_proc = CatProcedureType(code="trauma_norte", name="Trauma Norte Protocol")
    shortage = CatDiscrepancyReason(code="shortage", name="Shortage against expected inventory")
    types = [CatEventType(code=c, name=c) for c in ("auto_count", "manual_count", "correction_requested")]
    roles = {code: Role(code=code, description=desc, institution_id=inst.id) for code, desc in (
        ("it_admin", "IT Administrator"), ("operator_cde", "Operator CDE"), ("supervisor_quality", "Supervisor CDE / Quality"),
        ("trauma_lead", "Lider de Trauma Norte"))}
    db.session.add_all([*statuses.values(), scheduled, hemostasis, pre, appx, custom_proc, shortage, *types, *roles.values()])
    db.session.flush()
    users = {
        "operator": User(name="Oper Norte", email="op@norte.org", password_hash="x", institution_id=inst.id,
                         ui_preferences={"locale": "en", "theme": "dark", "density": "compact"}),
        "supervisor": User(name="Super Norte", email="sup@norte.org", password_hash="x", institution_id=inst.id,
                           ui_preferences={"locale": "en", "theme": "light"}),
        "admin": User(name="Admin Norte", email="adm@norte.org", password_hash="x", institution_id=inst.id,
                      ui_preferences={"locale": "en", "theme": "light"}),
        "bad_value": User(name="Bad Value", email="bad@norte.org", password_hash="x", institution_id=inst.id,
                          ui_preferences={"locale": "fr", "theme": "light"}),
        "no_locale": User(name="No Locale", email="none@norte.org", password_hash="x", institution_id=inst.id,
                          ui_preferences={"theme": "light"}),
        "numeric": User(name="Numeric Locale", email="num@norte.org", password_hash="x", institution_id=inst.id,
                        ui_preferences={"locale": 5}),
    }
    db.session.add_all(users.values()); db.session.flush()
    for key, code in (("operator", "operator_cde"), ("supervisor", "supervisor_quality"), ("admin", "it_admin")):
        db.session.add(UserRole(user_id=users[key].id, role_id=roles[code].id))
    kelly = InstrumentFamily(code="KELLY", name="Kelly Forceps", category_id=hemostasis.id,
                             function_text="Occlude small and medium-caliber vessels.")
    renamed = InstrumentFamily(code="MOSQUITO", name="Mosquito Long Custom", category_id=hemostasis.id)  # seeded code, renamed
    custom_family = InstrumentFamily(code="CLAMP_N", name="Trauma Clamp Norte", category_id=hemostasis.id)
    room = OperatingRoom(code="OR-N1", name="Quirofano Norte 1", institution_id=inst.id)
    db.session.add_all([kelly, renamed, custom_family, room]); db.session.flush()
    kit = Kit(name="Trauma Kit Norte", institution_id=inst.id)
    station = CaptureStation(name="Estacion Norte", room_id=room.id)
    db.session.add_all([kit, station]); db.session.flush()
    db.session.add_all([KitItem(kit_id=kit.id, family_id=kelly.id, quantity=6), KitItem(kit_id=kit.id, family_id=renamed.id, quantity=4),
                        ProcedureKit(procedure_type_id=appx.id, kit_id=kit.id, is_default=True),
                        ProcedurePhase(procedure_type_id=appx.id, phase_id=pre.id, sort_order=1)])
    operation = Operation(status_id=scheduled.id, procedure_type_id=appx.id, room_id=room.id, institution_id=inst.id)
    db.session.add(operation); db.session.flush()
    ws = WorkSession(status_id=statuses["validating"].id, user_id=users["operator"].id, operation_id=operation.id,
                     station_id=station.id, kit_id=kit.id, current_phase_id=pre.id)
    db.session.add(ws); db.session.flush()
    db.session.add_all([ExpectedInventory(session_id=ws.id, family_id=kelly.id, expected_quantity=6, source="kit_snapshot"),
                        ExpectedInventory(session_id=ws.id, family_id=renamed.id, expected_quantity=4, source="kit_snapshot")])
    run_id, now = str(uuid.uuid4()), datetime.now(timezone.utc)
    auto = CountEvent(session_id=ws.id, event_type_id=types[0].id, user_id=None, family_id=kelly.id, expected_quantity=6,
                      detected_quantity=5, occurred_at=now, client_event_id=uuid.uuid4(),
                      payload={"inference_run_id": run_id, "confidence_mean": 0.95, "model_version_tag": "e2e-controlled-dev",
                               "inference_provider": "controlled", "confidence_threshold": 0.7, "yolo_class_id": 0})
    auto2 = CountEvent(session_id=ws.id, event_type_id=types[0].id, user_id=None, family_id=renamed.id, expected_quantity=4,
                       detected_quantity=4, occurred_at=now, client_event_id=uuid.uuid4(),
                       payload={"inference_run_id": run_id, "confidence_mean": 0.9, "model_version_tag": "e2e-controlled-dev",
                                "inference_provider": "controlled", "confidence_threshold": 0.7, "yolo_class_id": 1})
    db.session.add_all([auto, auto2]); db.session.flush()
    manual = CountEvent(session_id=ws.id, event_type_id=types[1].id, user_id=users["operator"].id, family_id=kelly.id,
                        expected_quantity=6, detected_quantity=6, occurred_at=now + timedelta(seconds=5), client_event_id=uuid.uuid4(),
                        payload={"reviewed_event_id": str(auto.id), "root_event_id": str(auto.id), "validation_kind": "validation"})
    db.session.add(manual); db.session.flush()
    db.session.add(HumanCorrection(count_event_id=auto.id, user_id=users["operator"].id, justification=JUSTIFICATION, recorded_at=now))
    disc = Discrepancy(session_id=ws.id, origin_event_id=auto.id, family_id=kelly.id, expected_quantity=6, detected_quantity=5,
                       reason_id=shortage.id, resolved=False, description="Shortage: Kelly Forceps expected 6, detected 5 (-1).")
    db.session.add(disc)
    db.session.commit()
    I.update(inst=inst.id, ws=ws.id, disc=disc.id, kelly=kelly.id, appx=appx.id, **{k: v.id for k, v in users.items()})

CURRENT = {"user": None}
routes._current_user = lambda: CURRENT["user"]


def as_user(key, role=None):
    role = role or {"operator": "operator_cde", "supervisor": "supervisor_quality", "admin": "it_admin"}.get(key, "operator_cde")
    CURRENT["user"] = fake_user(I[key], I["inst"], role, key.title())


def anonymous():
    CURRENT["user"] = None


def lang(body):
    match = re.search(r'<html lang="([^"]+)"', body)
    return match.group(1) if match else None


def prefs(key):
    with flask_app.app_context():
        db.session.expire_all()
        return db.session.get(User, I[key]).ui_preferences


def canonical():
    with flask_app.app_context():
        return {
            "family": db.session.scalar(select(InstrumentFamily.name).where(InstrumentFamily.code == "KELLY")),
            "procedure": db.session.scalar(select(CatProcedureType.name).where(CatProcedureType.code == "appendectomy")),
            "status": db.session.scalar(select(CatSessionStatus.name).where(CatSessionStatus.code == "open")),
            "reason": db.session.scalar(select(CatDiscrepancyReason.name).where(CatDiscrepancyReason.code == "shortage")),
            "role_codes": sorted(db.session.execute(select(Role.code)).scalars()),
            "role_desc": db.session.scalar(select(Role.description).where(Role.code == "supervisor_quality")),
            "status_codes": sorted(db.session.execute(select(CatSessionStatus.code)).scalars()),
            "justification": db.session.scalar(select(HumanCorrection.justification)),
            "description": db.session.scalar(select(Discrepancy.description)),
        }


def switch(client, code, next_url):
    return client.post("/preferences/locale", data={"locale": code, "next": next_url})


BEFORE = canonical()

print("== 1. anonymous: default EN, switch to ES in the web session")
anon = flask_app.test_client()
body = anon.get("/").get_data(as_text=True)
check(lang(body) == "en" and "Sign In" in body and "Iniciar sesión" not in body, "landing defaults to English")
body = anon.get("/sign-in").get_data(as_text=True)
check(lang(body) == "en" and "Institutional Email" in body, "sign in defaults to English")
check('action="/preferences/locale"' in body and 'name="locale" value="es"' in body and 'aria-current="true"' in body,
      "language selector is a real POST control with the current locale marked")
r = switch(anon, "es", "/sign-in")
check(r.status_code == 303 and r.headers["Location"].endswith("/sign-in"), "switch redirects back to the same page")
with anon.session_transaction() as s:
    check(s.get("locale") == "es", "anonymous choice stored in the web session")
body = anon.get("/sign-in").get_data(as_text=True)
check(lang(body) == "es" and "Iniciar sesión" in body and "Correo electrónico institucional" in body, "sign in renders in Spanish")
check(re.search(r'value="es"\s+lang="es"\s+aria-current="true"', body) is not None, "ES option is the selected one")
body = anon.get("/").get_data(as_text=True)
check(lang(body) == "es" and "Cómo funciona" in body, "landing renders in Spanish")
check('"remove": "Quitar"' in body and 'id="pef-i18n"' in body, "JS messages are rendered localized once per page")

print("== 2. strict validation: invalid values are rejected and never stored")
for bad in ("fr", "es-MX", "EN", "", "es;drop"):
    r = switch(anon, bad, "/sign-in")
    check(r.status_code == 400, f"locale {bad!r} rejected with 400")
with anon.session_transaction() as s:
    check(s.get("locale") == "es", "session locale unchanged after invalid attempts")
for target in ("https://evil.example/x", "//evil.example/x", "/\\evil.example"):
    r = switch(anon, "es", target)
    check(r.status_code == 303 and r.headers["Location"] in ("/", "http://localhost/"), f"no open redirect for {target!r}")
with anon.session_transaction() as s:
    s["locale"] = "de"
check(lang(anon.get("/").get_data(as_text=True)) == "en", "tampered session locale falls back to English")
with anon.session_transaction() as s:
    s["locale"] = "es"

print("== 3. sign-in (auth service mocked): the signed-in user's preference wins over the session")


class FakeAuth:
    ok, status_code, raw = True, 200, None

    def json(self):
        return {"user": {"roles": [{"code": "operator_cde"}]}}


real_auth = routes._auth_request
routes._auth_request = lambda method, path, **kw: FakeAuth() if path == "/login" else None
r = anon.post("/sign-in", data={"institutional_email": "op@norte.org", "password": "x"})
routes._auth_request = real_auth
check(r.status_code == 303 and r.headers["Location"].endswith("/operator/dashboard"), "login still works")
as_user("operator")
body = anon.get("/operator/dashboard").get_data(as_text=True)
check(lang(body) == "en" and "Dashboard" in body, "operator UI uses the stored preference (en), not the anonymous session")

print("== 4. authenticated operator selects ES: persisted, other preferences kept")
op = flask_app.test_client()
r = switch(op, "es", "/operator/sessions?status=open")
check(r.status_code == 303 and r.headers["Location"].endswith("/operator/sessions?status=open"), "stays on the same page (query kept)")
check(prefs("operator") == {"locale": "es", "theme": "dark", "density": "compact"}, "only ui_preferences.locale changed")
body = op.get("/operator/sessions").get_data(as_text=True)
check(lang(body) == "es" and "Sesiones de conteo" in body, "current screen is Spanish")
check("Apendicectomía" in body and "Appendectomy" not in body, "procedure translated by code (appendectomy)")
check("En validación" in body, "session status translated by code (validating)")
check("Trauma Kit Norte" in body, "user-created kit name shown as stored")
fresh = flask_app.test_client()  # no web-session cookie at all: comes from the database
for path, marker in (("/operator/dashboard", "Panel"), ("/operator/sessions/history", "Historial de sesiones"),
                     ("/operator/profile", "Mi perfil"), ("/operator/sessions/new", "Nueva sesión de conteo")):
    body = fresh.get(path).get_data(as_text=True)
    check(lang(body) == "es" and marker in body, f"{path} stays Spanish on a new request")
body = fresh.get("/operator/profile").get_data(as_text=True)
check("Operador CDE" in body and ">Español<" in body, "profile: role name by code + current language")
body = fresh.get("/operator/sessions/new").get_data(as_text=True)
check("Pinza Kelly" in body and "Mosquito Long Custom" in body and "Conteo inicial antes de la incisión" in body,
      "new session: seeded family/phase by code, renamed seeded family as stored")
check("Tijera" not in body and "Trauma Kit Norte" in body, "no text-matching translation of other values")
body = fresh.get(f"/operator/sessions/{I['ws']}/validation").get_data(as_text=True)
check(lang(body) == "es" and "Validación humana" in body and "Pinza Kelly" in body, "validation screen Spanish")
body = fresh.get(f"/operator/sessions/{I['ws']}/ai-detection").get_data(as_text=True)
check("Conteo sugerido por IA" in body and "Inferencia controlada (proveedor de desarrollo)" in body and "e2e-controlled-dev" in body,
      "AI Suggested Count localized; model version tag untouched")
check("Faltante" in body, "AI result badge localized by reason code")
body = fresh.get(f"/operator/sessions/{I['ws']}/awaiting-review").get_data(as_text=True)
check(JUSTIFICATION in body and "Faltante respecto al inventario esperado" in body, "free-text justification unchanged; reason by code")

print("== 5. sign out keeps ES for the public pages; sign in again restores ES")
r = op.get("/sign-out")
anonymous()
with op.session_transaction() as s:
    check(s.get("locale") == "es", "locale kept in the web session after sign-out")
body = op.get("/sign-in").get_data(as_text=True)
check(lang(body) == "es" and "Iniciar sesión" in body, "sign-in page stays Spanish after logout")
as_user("operator")
body = flask_app.test_client().get("/operator/dashboard").get_data(as_text=True)
check(lang(body) == "es", "sign in again -> Spanish preference restored")

print("== 6. operator switches back to EN")
r = switch(op, "en", "/operator/dashboard")
check(r.status_code == 303 and prefs("operator") == {"locale": "en", "theme": "dark", "density": "compact"}, "EN persisted, other keys kept")
body = flask_app.test_client().get("/operator/sessions").get_data(as_text=True)
check(lang(body) == "en" and "Appendectomy" in body and "Counting Sessions" in body, "subsequent requests are English")

print("== 7. supervisor in both languages")
as_user("supervisor")
sup = flask_app.test_client()
body = sup.get("/supervisor/discrepancies").get_data(as_text=True)
check(lang(body) == "en" and "Under Review" in body and "Kelly Forceps" in body, "supervisor EN")
switch(sup, "es", "/supervisor/discrepancies")
check(prefs("supervisor") == {"locale": "es", "theme": "light"}, "supervisor preference persisted")
body = sup.get("/supervisor/discrepancies").get_data(as_text=True)
check(lang(body) == "es" and "En revisión" in body and "Pinza Kelly" in body and "Discrepancias" in body, "supervisor ES")
body = sup.get(f"/supervisor/discrepancies/{I['ws']}/review").get_data(as_text=True)
check("Aprobar" in body and "Solicitar corrección" in body and "Rechazar" in body and JUSTIFICATION in body, "review modals ES, free text kept")
body = sup.get("/supervisor/profile").get_data(as_text=True)
check("Supervisor CDE / Supervisor de Calidad" in body and "Supervisor CDE / Quality" not in body, "role label by code, not description")
for path in ("/supervisor/dashboard", "/supervisor/sessions", "/supervisor/reports", "/supervisor/indicators", "/supervisor/audit-log",
             f"/supervisor/sessions/{I['ws']}"):
    r = sup.get(path)
    check(r.status_code == 200 and lang(r.get_data(as_text=True)) == "es", f"{path} renders in Spanish")
body = sup.get("/supervisor/reports").get_data(as_text=True)
check("Todo el historial" in body and "Últimos 7 días" in body, "report filters localized")
switch(sup, "en", "/supervisor/profile")
body = sup.get("/supervisor/profile").get_data(as_text=True)
check(lang(body) == "en" and "CDE Supervisor / Quality Supervisor" in body, "supervisor back to EN")

print("== 8. administrator in both languages")
as_user("admin")
adm = flask_app.test_client()
body = adm.get("/admin/instrument-families").get_data(as_text=True)
check(lang(body) == "en" and "Kelly Forceps" in body and "Occlude small and medium-caliber vessels." in body, "admin families EN")
switch(adm, "es", "/admin/instrument-families")
body = adm.get("/admin/instrument-families").get_data(as_text=True)
check(lang(body) == "es" and "Pinza Kelly" in body and "Ocluir vasos de calibre pequeño y mediano." in body, "seeded family + function by code")
check("Trauma Clamp Norte" in body and "Mosquito Long Custom" in body and "Hemostasia" in body, "custom/renamed as stored; category by code")
body = adm.get(f"/admin/instrument-families/{I['kelly']}/edit").get_data(as_text=True)
check('value="Kelly Forceps"' in body and "Pinza Kelly" in body, "edit form keeps the stored canonical value, shows the localized name")
body = adm.get("/admin/procedures").get_data(as_text=True)
check("Apendicectomía" in body and "Trauma Norte Protocol" in body, "procedures: seeded by code, custom as stored")
body = adm.get("/admin/roles").get_data(as_text=True)
check("Supervisor CDE / Calidad" in body and "Lider de Trauma Norte" in body, "role description: seeded localized, custom as stored")
body = adm.get("/admin/users").get_data(as_text=True)
check("Administrador de TI" in body and "Operador CDE" in body, "user role chips by role code")
for path in ("/admin/dashboard", "/admin/instruments", "/admin/kits", "/admin/kits/new", "/admin/users/new", "/admin/roles/new",
             "/admin/vision-models", "/admin/vision-models/new", "/admin/audit-log", "/admin/configuration",
             "/admin/configuration/operating-rooms/new", "/admin/configuration/capture-stations/new", "/admin/procedures/new",
             "/admin/instrument-families/new", "/admin/instruments/new", "/admin/profile"):
    r = adm.get(path)
    check(r.status_code == 200 and lang(r.get_data(as_text=True)) == "es", f"{path} renders in Spanish")
body = adm.get("/admin/dashboard").get_data(as_text=True)
check("Panel" in body and "Resumen de catálogos" in body, "admin dashboard labels localized")
r = adm.post("/admin/kits/new", data={"name": "", "status": "active"})
check(r.status_code == 400 and "Nombre del kit es obligatorio." in r.get_data(as_text=True), "validation feedback localized")
switch(adm, "en", "/admin/dashboard")
check(lang(adm.get("/admin/dashboard").get_data(as_text=True)) == "en", "admin back to EN")

print("== 9. malformed / missing preferences fall back to English")
for key in ("bad_value", "no_locale", "numeric"):
    as_user(key, "operator_cde")
    r = flask_app.test_client().get("/operator/dashboard")
    check(r.status_code == 200 and lang(r.get_data(as_text=True)) == "en", f"{key} -> English, no error")
CURRENT["user"] = fake_user(uuid.uuid4(), I["inst"], "operator_cde", "Ghost")
check(lang(flask_app.test_client().get("/operator/dashboard").get_data(as_text=True)) == "en", "user row missing -> English")

print("== 10. labels: by code, fallbacks, formatting")
with flask_app.test_request_context("/"):
    from flask import g
    from flask_babel import gettext, refresh
    g.pef_locale = "es"; refresh()
    check(labels.label("session_status", "open", "Open") == "Abierta", "session_status.open -> Abierta")
    check(labels.label("procedure_type", "appendectomy", "Appendectomy") == "Apendicectomía", "procedure.appendectomy")
    check(labels.label("procedure_type", "appendectomy", "Appendectomy (custom)") == "Appendectomy (custom)", "renamed seeded row as stored")
    check(labels.label("discrepancy_reason", "low_confidence") == "Confianza del modelo por debajo del umbral", "reason by code")
    check(labels.role_name("operator_cde") == "Operador CDE" and labels.role_name("it_admin", "whatever") == "Administrador de TI", "role by code")
    check(labels.role_name("trauma_lead", "Lider de Trauma Norte") == "Lider de Trauma Norte", "custom role -> stored description")
    check(labels.label("session_status", "zz_custom", "Custom Status") == "Custom Status", "unknown code -> stored value")
    check(labels.label("no_such_catalog", "x") == "x", "unknown catalog -> code, no KeyError")
    check(gettext("A sentence that is not in the catalog") == "A sentence that is not in the catalog", "missing translation -> English")
    check(labels.label("review_status", "UNDER_REVIEW") == "En revisión" and labels.active_label(False) == "Inactivo", "workflow labels")
    check(labels.format_percentage(80.0) in ("80.0 %", "80.0 %", "80.0%") and labels.format_day(datetime(2026, 9, 28).date()) == "lun 28",
          "percent + weekday localized")
    g.pef_locale = "en"; refresh()
    check(labels.label("session_status", "open", "Open") == "Open" and labels.format_percentage(80.0) == "80.0%", "EN labels + formatting")
from sqlalchemy.dialects import postgresql  # noqa: E402

compiled = localization.user_locale_update(I["operator"], "es", "postgresql").compile(dialect=postgresql.dialect())
pg_sql = str(compiled)
check('SET ui_preferences=("user".ui_preferences || CAST(' in pg_sql and "AS JSONB))" in pg_sql and "WHERE" in pg_sql,
      "PostgreSQL: one UPDATE merging a JSONB value into ui_preferences")
jsonb_params = [value for key, value in compiled.params.items() if key != "id_1"]
bound = postgresql.JSONB().bind_processor(postgresql.dialect())(jsonb_params[0]) if jsonb_params else None
check(jsonb_params == [{"locale": "es"}] and json.loads(bound) == {"locale": "es"},
      "PostgreSQL: the merged value is the JSON object {\"locale\": \"es\"} (not a JSON-encoded string)")
try:
    import pglast
    pglast.parse_sql(re.sub(r"%\(\w+\)s", "$1", pg_sql))
    check(True, "PostgreSQL grammar accepts the ui_preferences UPDATE (pglast)")
except ImportError:
    print("  (pglast not installed: PostgreSQL grammar check skipped)")
check(localization.normalize_locale("es") == "es" and localization.normalize_locale("es-MX") is None
      and localization.normalize_locale(None) is None, "normalize_locale is strict")

print("== 11. database safety: rendering Spanish never changes canonical data")
AFTER = canonical()
check(AFTER == BEFORE, "catalog names, codes, role descriptions and free text unchanged")
check(AFTER["family"] == "Kelly Forceps" and AFTER["procedure"] == "Appendectomy" and AFTER["status"] == "Open", "canonical English kept")
check(AFTER["role_codes"] == ["it_admin", "operator_cde", "supervisor_quality", "trauma_lead"], "stable role codes")
check(AFTER["description"].startswith("Shortage: Kelly Forceps"), "persisted discrepancy description not rewritten")

print("== 12. gettext catalogs: complete, compiled, placeholders consistent, every catalog code labelled")
from babel.messages.extract import extract_from_dir  # noqa: E402
from babel.messages.mofile import read_mo  # noqa: E402
from babel.messages.pofile import read_po  # noqa: E402

po_path = Path(APP) / "translations" / "es" / "LC_MESSAGES" / "messages.po"
po = read_po(open(po_path, "rb"))
entries = [m for m in po if m.id]
untranslated = [m.id for m in entries if not (all(m.string) if isinstance(m.string, tuple) else m.string)]
check(not untranslated and not [m for m in entries if m.fuzzy], f"{len(entries)} Spanish entries, none untranslated/fuzzy {untranslated[:3]}")
placeholder = re.compile(r"%\(\w+\)[sd]")
bad = [m.id for m in entries if not isinstance(m.id, tuple) and sorted(placeholder.findall(m.id)) != sorted(placeholder.findall(m.string))]
check(not bad, f"placeholders identical in msgid/msgstr {bad[:3]}")
mo = read_mo(open(po_path.with_suffix(".mo"), "rb"))


def as_key(value):
    return tuple(value) if isinstance(value, (list, tuple)) else value


def as_text(value):  # babel's read_mo returns msgctxt as bytes
    return value.decode("utf-8") if isinstance(value, bytes) else value


check({(as_text(m.context), as_key(m.id)): as_key(m.string) for m in mo if m.id} == {(m.context, as_key(m.id)): as_key(m.string) for m in entries},
      "messages.mo compiled from messages.po")
source_keys = set()
method_map = [("controllers/**.py", "python"), ("services/**.py", "python"), ("localization/**.py", "python"), ("templates/**.html", "jinja2")]
for _file, _line, message, _comments, context in extract_from_dir(APP, method_map, keywords={
        "_": None, "gettext": None, "ngettext": (1, 2), "pgettext": ((1, "c"), 2), "_c": ((1, "c"), 2)}):
    source_keys.add((context, message))
po_keys = {(m.context, m.id) for m in entries}
check(source_keys <= po_keys, f"every UI string in the source is translated ({len(source_keys - po_keys)} missing: {sorted(source_keys - po_keys, key=str)[:3]})")
seed = (Path(APP).parents[1] / "data" / "seeds" / "002_seed_catalogs.sql").read_text(encoding="utf-8")
catalog_names = {"cat_gender": "gender", "cat_specialty": "specialty", "cat_procedure_type": "procedure_type",
                 "cat_surgical_role": "surgical_role", "cat_operation_status": "operation_status", "cat_session_status": "session_status",
                 "cat_instrument_cycle_status": "instrument_cycle_status", "cat_instrument_category": "instrument_category",
                 "cat_usage_context": "usage_context", "cat_discrepancy_reason": "discrepancy_reason",
                 "cat_checkpoint_reason": "checkpoint_reason", "cat_operation_phase": "operation_phase", "cat_event_type": "event_type",
                 "cat_processing_purpose": "processing_purpose"}
missing_codes = []
for table, catalog in catalog_names.items():
    block = seed[seed.index(f"INSERT INTO {table} "):]
    block = block[:block.index(";")]
    for code, name in re.findall(r"\('([a-z_]+)',\s*'([^']+)'", block):
        if labels.CATALOGS[catalog].get(code) != name:
            missing_codes.append(f"{catalog}.{code}")
check(not missing_codes, f"every 002 catalog code has a label equal to its canonical English name {missing_codes[:3]}")
check(all((ctx, en) in po_keys for ctx, entries_ in labels.CATALOGS.items() for en in entries_.values()), "every code label has a Spanish entry")

finish()
