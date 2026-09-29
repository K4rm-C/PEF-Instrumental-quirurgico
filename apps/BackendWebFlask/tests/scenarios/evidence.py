"""Strict ProcedureKit + capture evidence (local storage, MediaAsset, open->counting, audit) tests."""
import hashlib
import io
import os
import re
import shutil
import uuid

from _common import HERE, check, fake_user, finish

STORE = os.path.join(HERE, "evidence_store")
shutil.rmtree(STORE, ignore_errors=True)
os.environ["CAPTURE_STORAGE_ROOT"] = STORE
os.environ["CAPTURE_MAX_BYTES"] = "60000"

from _common import setup_app  # noqa: E402

flask_app, db = setup_app("smoke_evidence.db")

from sqlalchemy import event, func, select  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import controllers.routes as routes  # noqa: E402
import services.evidence_service as evidence  # noqa: E402
from models.AccessAudit import AccessAudit  # noqa: E402
from models.CaptureStation import CaptureStation  # noqa: E402
from models.CatInstrumentCategory import CatInstrumentCategory  # noqa: E402
from models.CatOperationPhase import CatOperationPhase  # noqa: E402
from models.CatOperationStatus import CatOperationStatus  # noqa: E402
from models.CatProcedureType import CatProcedureType  # noqa: E402
from models.CatSessionStatus import CatSessionStatus  # noqa: E402
from models.Institution import Institution  # noqa: E402
from models.InstrumentFamily import InstrumentFamily  # noqa: E402
from models.Kit import Kit  # noqa: E402
from models.KitItem import KitItem  # noqa: E402
from models.MediaAsset import MediaAsset  # noqa: E402
from models.OperatingRoom import OperatingRoom  # noqa: E402
from models.Operation import Operation  # noqa: E402
from models.ProcedureKit import ProcedureKit  # noqa: E402
from models.ProcedurePhase import ProcedurePhase  # noqa: E402
from models.User import User  # noqa: E402
from models.WorkSession import WorkSession  # noqa: E402

I = {}
with flask_app.app_context():
    a, b = Institution(name="A"), Institution(name="B")
    db.session.add_all([a, b]); db.session.flush()
    st_open, st_counting, st_closed = (CatSessionStatus(code=c, name=c) for c in ("open", "counting", "closed"))
    sched = CatOperationStatus(code="scheduled", name="Scheduled")
    cat = CatInstrumentCategory(code="c", name="c")
    phase = CatOperationPhase(code="pre", name="Pre")
    proc, proc_nokit = CatProcedureType(code="P1", name="Proc 1"), CatProcedureType(code="P2", name="Procedure Without Kit")
    db.session.add_all([st_open, st_counting, st_closed, sched, cat, phase, proc, proc_nokit]); db.session.flush()
    op_user = User(name="Op A", email="a@a.org", password_hash="x", institution_id=a.id)
    op_b = User(name="Op B", email="b@b.org", password_hash="x", institution_id=b.id)
    fam = InstrumentFamily(code="F", name="Kelly", category_id=cat.id)
    room, room_b = OperatingRoom(code="R", name="R", institution_id=a.id), OperatingRoom(code="RB", name="RB", institution_id=b.id)
    db.session.add_all([op_user, op_b, fam, room, room_b]); db.session.flush()
    kit, kit_b = Kit(name="Kit A", institution_id=a.id), Kit(name="Kit B", institution_id=b.id)
    station, station_b = CaptureStation(name="S", room_id=room.id), CaptureStation(name="SB", room_id=room_b.id)
    db.session.add_all([kit, kit_b, station, station_b]); db.session.flush()
    db.session.add_all([KitItem(kit_id=kit.id, family_id=fam.id, quantity=6), KitItem(kit_id=kit_b.id, family_id=fam.id, quantity=1),
                        ProcedureKit(procedure_type_id=proc.id, kit_id=kit.id, is_default=True),
                        ProcedurePhase(procedure_type_id=proc.id, phase_id=phase.id, sort_order=1)])
    op = Operation(status_id=sched.id, procedure_type_id=proc.id, room_id=room.id, institution_id=a.id)
    op_nokit = Operation(status_id=sched.id, procedure_type_id=proc_nokit.id, room_id=room.id, institution_id=a.id)
    op_noproc = Operation(status_id=sched.id, procedure_type_id=None, room_id=room.id, institution_id=a.id)
    op_b_row = Operation(status_id=sched.id, procedure_type_id=proc.id, room_id=room_b.id, institution_id=b.id)
    db.session.add_all([op, op_nokit, op_noproc, op_b_row]); db.session.flush()
    ws_b = WorkSession(status_id=st_open.id, user_id=op_b.id, operation_id=op_b_row.id, station_id=station_b.id, kit_id=kit_b.id)
    ws_closed = WorkSession(status_id=st_closed.id, user_id=op_user.id, operation_id=op.id, station_id=station.id, kit_id=kit.id)
    db.session.add_all([ws_b, ws_closed]); db.session.commit()
    I.update(a=a.id, op_user=op_user.id, kit=kit.id, station=station.id, op=op.id, op_nokit=op_nokit.id, op_noproc=op_noproc.id,
             phase=phase.id, ws_b=ws_b.id, ws_closed=ws_closed.id)

routes._current_user = lambda: fake_user(I["op_user"], I["a"], "operator_cde", "Op A")
client = flask_app.test_client()


def count(model, **filters):
    with flask_app.app_context():
        return db.session.scalar(select(func.count()).select_from(model).filter_by(**filters))


def files_on_disk():
    return sorted(os.path.relpath(os.path.join(d, f), STORE).replace("\\", "/")
                  for d, _, fs in os.walk(STORE) for f in fs)


def jpeg(size=400, seed=b"x"):
    body = (seed * size)[:size]
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + body + b"\xff\xd9"


def upload(session_id, data, name="tray.jpg", mimetype="image/jpeg"):
    return client.post(f"/operator/sessions/{session_id}/capture",
                       data={"capture_image": (io.BytesIO(data), name, mimetype)}, content_type="multipart/form-data")


def start_session(**overrides):
    data = {"operation_id": str(I["op"]), "kit_id": str(I["kit"]), "capture_station_id": str(I["station"]),
            "counting_phase": str(I["phase"]), "instrument_readiness_confirmed": "on"}
    data.update(overrides)
    return client.post("/operator/sessions/new", data=data)


def status_code_of(ws_id):
    with flask_app.app_context():
        ws = db.session.get(WorkSession, ws_id)
        return db.session.get(CatSessionStatus, ws.status_id).code


print("== 1. Strict ProcedureKit")
n = count(WorkSession)
page = client.get(f"/operator/sessions/new?operation_id={I['op_nokit']}").get_data(as_text=True)
check("No active kit is associated with this procedure" in page and "Kit A" not in page, "GET: clear notice, no arbitrary kits")
check(start_session(operation_id=str(I["op_nokit"])).status_code == 409, "POST procedure without ProcedureKit -> 409")
page = client.get(f"/operator/sessions/new?operation_id={I['op_noproc']}").get_data(as_text=True)
check("has no procedure type" in page, "GET: operation without procedure explained")
check(start_session(operation_id=str(I["op_noproc"])).status_code == 409, "POST operation without procedure -> 409")
with flask_app.app_context():
    pk = db.session.execute(select(ProcedureKit)).scalar_one()
    pk.active = False; db.session.commit()
check(start_session().status_code == 409, "inactive ProcedureKit -> 409")
with flask_app.app_context():
    pk = db.session.execute(select(ProcedureKit)).scalar_one()
    pk.active = True; db.session.commit()
check(count(WorkSession) == n, "no WorkSession created by rejected POSTs")

resp = start_session()
ws_id = uuid.UUID(re.search(r"/operator/sessions/([0-9a-f-]{36})/capture", resp.headers["Location"]).group(1))
resp2 = start_session()
ws2_id = uuid.UUID(re.search(r"/operator/sessions/([0-9a-f-]{36})/capture", resp2.headers["Location"]).group(1))

print("== 2. Empty capture state")
body = client.get(f"/operator/sessions/{ws_id}/capture").get_data(as_text=True)
check("No capture yet" in body and "No capture has been taken" in body and "<img id=\"capture-latest\"" not in body, "real empty state")
check('name="capture_image"' in body and 'accept="image/jpeg"' in body and "disabled>" in body, "upload control present")

print("== 3-11. First capture")
data1 = jpeg(500, b"a")
r = upload(ws_id, data1, name="../../etc/passwd.jpg")
check(r.status_code == 302, f"upload -> {r.status_code}")
with flask_app.app_context():
    assets = db.session.execute(select(MediaAsset)).scalars().all()
    check(len(assets) == 1, "MediaAsset created")
    asset = assets[0]
    expected_prefix = f"institutions/{I['a']}/sessions/{ws_id}/captures/"
    check(asset.object_key == f"{expected_prefix}{asset.id}.jpg", "object_key namespaced institution/session/<asset uuid>.jpg")
    check("passwd" not in asset.object_key and ".." not in asset.object_key, "user filename not used")
    check(asset.storage_provider == "other" and asset.kind == "jpeg" and asset.content_type == "image/jpeg", "provider other / kind jpeg")
    check(asset.gcs_uri == f"local://pef-evidence/{asset.object_key}" and asset.bucket == "pef-evidence", "logical local URI")
    check(asset.sha256 == hashlib.sha256(data1).hexdigest(), "sha256 correct")
    check(asset.size_bytes == len(data1), "size_bytes correct")
    path = os.path.join(STORE, *asset.object_key.split("/"))
    check(os.path.isfile(path) and open(path, "rb").read() == data1, "physical file written with same bytes")
    check(not STORE.replace("\\", "/").endswith("/static") and "static" not in os.path.relpath(STORE, flask_app.root_path), "storage outside /static")
    first_asset = asset.id
check(status_code_of(ws_id) == "counting", "open -> counting on first capture")
check(count(AccessAudit, action="START_COUNT", resource_id=ws_id) == 1, "START_COUNT audited")
check(count(AccessAudit, action="CAPTURE_EVIDENCE", resource_id=first_asset) == 1, "CAPTURE_EVIDENCE audited")

print("== 12-13. Second capture")
data2 = jpeg(700, b"b")
check(upload(ws_id, data2).status_code == 302, "second upload ok")
check(count(AccessAudit, action="START_COUNT", resource_id=ws_id) == 1, "START_COUNT not duplicated")
check(count(AccessAudit, action="CAPTURE_EVIDENCE") == 2 and count(MediaAsset) == 2, "second asset kept + audited")
check(len(files_on_disk()) == 2, "both files kept on disk")
check(status_code_of(ws_id) == "counting", "still counting")

print("== 24. latest-capture helper")
with flask_app.app_context(), flask_app.test_request_context():
    latest = evidence.get_latest_capture_for_session(db.session.get(WorkSession, ws_id))
    check(latest is not None and latest.sha256 == hashlib.sha256(data2).hexdigest() and latest.size_bytes == len(data2)
          and latest.path.is_file() and latest.content_type == "image/jpeg" and latest.media_asset_id != first_asset,
          "get_latest_capture_for_session returns the newest capture with safe path")
    check(evidence.get_latest_capture_for_session(db.session.get(WorkSession, ws2_id)) is None, "no capture -> None")
    latest_id = latest.media_asset_id

print("== 22-23. Serving + page")
r = client.get(f"/operator/sessions/{ws_id}/media/{latest_id}")
check(r.status_code == 200 and r.data == data2 and r.mimetype == "image/jpeg", "authenticated route returns the exact JPEG")
check("no-store" in r.headers.get("Cache-Control", "") and STORE not in r.headers.get("Content-Disposition", ""), "private, no path leak")
body = client.get(f"/operator/sessions/{ws_id}/capture").get_data(as_text=True)
check(f"/operator/sessions/{ws_id}/media/{latest_id}" in body and "Evidence captured" in body, "capture page shows real latest capture")
check("No capture yet" not in body and body.count("/media/") >= 3, "pending notice gone; captures listed")

print("== 14-16. Invalid files")
before_files, before_assets = files_on_disk(), count(MediaAsset)
check(upload(ws_id, b"").status_code == 400, "empty file -> 400")
check(upload(ws_id, b"\x89PNG\r\n\x1a\n" + b"x" * 500, name="fake.jpg").status_code == 400, "PNG disguised as .jpg -> 400")
check(upload(ws_id, b"MZ" + b"x" * 500, name="evil.exe", mimetype="image/jpeg").status_code == 400, "non-JPEG bytes with image/jpeg -> 400")
check(upload(ws_id, jpeg(500), mimetype="text/plain").status_code == 400, "wrong MIME -> 400")
check(upload(ws_id, jpeg(60100)).status_code == 413, "over CAPTURE_MAX_BYTES -> 413")
check(upload(ws_id, jpeg(400000)).status_code == 413, "over MAX_CONTENT_LENGTH -> 413")
check(client.post(f"/operator/sessions/{ws_id}/capture", data={}).status_code == 400, "no file -> 400")
check(files_on_disk() == before_files and count(MediaAsset) == before_assets, "no files/assets from rejected uploads")

print("== 17-19. Scope + closed")
check(upload(I["ws_b"], jpeg()).status_code == 404, "upload to other institution session -> 404")
check(upload(uuid.uuid4(), jpeg()).status_code == 404, "upload to unknown session -> 404")
check(upload(I["ws_closed"], jpeg()).status_code == 409, "upload to closed session -> 409")
check(client.get(f"/operator/sessions/{ws2_id}/media/{latest_id}").status_code == 404, "asset of another session -> 404")
check(client.get(f"/operator/sessions/{I['ws_b']}/media/{latest_id}").status_code == 404, "via foreign session -> 404")
check(client.get(f"/operator/sessions/{ws_id}/media/..%2F..%2Fsecret").status_code == 404, "path-like asset id -> 404")
with flask_app.app_context(), flask_app.test_request_context():
    try:
        evidence.resolve_object_path("../../outside.jpg")
        check(False, "traversal object_key rejected")
    except ValueError:
        check(True, "traversal object_key rejected")
check(files_on_disk() == before_files, "no files written by rejected uploads")

print("== 20. DB failure after write -> file compensated")
original = evidence.record_audit


def boom(*args, **kwargs):
    raise SQLAlchemyError("simulated audit failure")


evidence.record_audit = boom
r = upload(ws2_id, jpeg(300, b"c"))
evidence.record_audit = original
check(r.status_code == 503, f"staging failure -> {r.status_code}")
check(files_on_disk() == before_files and count(MediaAsset) == before_assets, "file removed, no MediaAsset")
check(status_code_of(ws2_id) == "open", "session status unchanged")


def fail_commit(session):
    raise SQLAlchemyError("simulated commit failure")


event.listen(Session, "before_commit", fail_commit)
r = upload(ws2_id, jpeg(300, b"d"))
event.remove(Session, "before_commit", fail_commit)
check(r.status_code == 503 and files_on_disk() == before_files and count(MediaAsset) == before_assets,
      "commit failure -> file removed, no MediaAsset")
check(status_code_of(ws2_id) == "open" and count(AccessAudit, action="START_COUNT", resource_id=ws2_id) == 0, "no transition/audit")

print("== 21. Missing 'counting' catalog")
with flask_app.app_context():
    row = db.session.execute(select(CatSessionStatus).where(CatSessionStatus.code == "counting")).scalar_one()
    row.code = "tmp_counting"; db.session.commit()
r = upload(ws2_id, jpeg(300, b"e"))
check(r.status_code == 409 and files_on_disk() == before_files and count(MediaAsset) == before_assets, "409, nothing stored")
check(status_code_of(ws2_id) == "open", "status still open")
with flask_app.app_context():
    row = db.session.execute(select(CatSessionStatus).where(CatSessionStatus.code == "tmp_counting")).scalar_one()
    row.code = "counting"; db.session.commit()
check(upload(ws2_id, jpeg(300, b"f")).status_code == 302 and status_code_of(ws2_id) == "counting", "works again once catalog restored")

shutil.rmtree(STORE, ignore_errors=True)
finish()
