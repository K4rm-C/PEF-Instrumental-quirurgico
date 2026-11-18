"""POST /api/v1/evidence (Phase C) with an in-memory storage fake and the rolled-back PostgreSQL transaction."""

from __future__ import annotations

import hashlib
import io
import logging
import os
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from conftest import FakeStorage, TransactionalDatabase, make_settings
from evidence_fixtures import audits, edata  # noqa: F401  (fixture)
from main import create_app

URL = "/api/v1/evidence"
FIELDS = ["id", "session_id", "kind", "content_type", "size_bytes", "sha256", "storage_provider",
          "retention_until", "created_at", "blocked", "purged", "signed_url", "signed_url_expires_at"]
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + os.urandom(300) + b"\xff\xd9"
GIF89 = b"GIF89a\x01\x00\x01\x00" + os.urandom(200) + b";"
GIF87 = b"GIF87a\x01\x00\x01\x00" + os.urandom(200) + b";"


def _form(session_id, kind="jpeg", data=JPEG, filename="capture.jpg", content_type="image/jpeg", **extra):
    form = {"session_id": str(session_id), "kind": kind, "file": (io.BytesIO(data), filename, content_type)}
    form.update(extra)
    return form


def _post(client, headers, form):
    return client.post(URL, data=form, content_type="multipart/form-data", headers=headers)


def _assets(db_conn, session_id):
    return [dict(r) for r in db_conn.execute(text("SELECT * FROM media_asset WHERE session_id = :s ORDER BY created_at"),
                                             {"s": session_id}).mappings()]


def _upload_audits(db_conn):
    return [a for a in audits(db_conn) if a["action"] == "evidence.upload"]


def _assert_nothing_persisted(db_conn, storage, session_id):
    assert _assets(db_conn, session_id) == [] and _upload_audits(db_conn) == [] and storage.objects == {}


@pytest.fixture
def upload_app(db_conn, redis_client):
    """Factory: (storage fake, settings overrides) → test client."""
    def build(storage=None, **settings):
        storage = storage or FakeStorage()
        app = create_app(make_settings(**settings), database=TransactionalDatabase(db_conn),
                         redis_client=redis_client, storage=storage)
        return app.test_client(), storage
    return build


@pytest.fixture
def tempfiles(monkeypatch):
    """Tracks every on-disk temporary file the upload path creates."""
    import uploads

    created = []
    original = uploads.new_upload_tempfile

    def tracking():
        handle = original()
        created.append(handle)
        return handle
    monkeypatch.setattr(uploads, "new_upload_tempfile", tracking)
    return created


# --- Success -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind, data, content_type, extension", [
    ("jpeg", JPEG, "image/jpeg", "jpg"), ("gif", GIF89, "image/gif", "gif"), ("gif", GIF87, "image/gif", "gif"),
])
def test_upload_success(upload_app, world, edata, as_user, db_conn, kind, data, content_type, extension):
    client, storage = upload_app()
    session = edata.sessions["a1"]
    started = datetime.now(timezone.utc)
    response = _post(client, as_user("op_a", **{"X-Correlation-ID": "corr-upload-1"}), _form(session.id, kind, data))
    assert response.status_code == 201, response.get_data(as_text=True)
    body = response.get_json()
    assert list(body) == FIELDS
    sha = hashlib.sha256(data).hexdigest()
    assert body["session_id"] == str(session.id) and body["kind"] == kind and body["content_type"] == content_type
    assert body["size_bytes"] == len(data) and body["sha256"] == sha and body["storage_provider"] == "other"
    assert body["blocked"] is False and body["purged"] is False
    assert body["signed_url"] is None and body["signed_url_expires_at"] is None       # POST never signs
    for internal in ("bucket", "object_key", "gcs_uri", "s3://"):
        assert internal not in response.get_data(as_text=True)

    [row] = [r for r in _assets(db_conn, session.id) if str(r["id"]) == body["id"]]
    year = datetime.now(timezone.utc).year
    key = f"{world.institutions['A']}/{session.id}/{year}/{body['id']}.{extension}"
    assert row["object_key"] == key and row["bucket"] == "evidence" and row["gcs_uri"] == f"s3://evidence/{key}"
    assert (row["content_type"], row["kind"], row["storage_provider"], row["sha256"], row["size_bytes"]) == (
        content_type, kind, "other", sha, len(data))
    assert row["blocked_at"] is None and row["purge_requested_at"] is None and row["purged_at"] is None
    assert started - timedelta(seconds=1) <= row["created_at"] <= datetime.now(timezone.utc) + timedelta(seconds=1)
    assert row["retention_until"] - row["created_at"] == timedelta(days=90)
    assert storage.objects == {key: (data, content_type)}                                 # exact bytes and type
    assert storage.calls == [f"put:{key}", f"head:{key}"]                                # no delete, no signing
    [audit] = _upload_audits(db_conn)
    assert audit == {"action": "evidence.upload", "resource_type": "media_asset", "resource_id": row["id"],
                     "actor_type": "user", "actor_user_id": world.users["op_a"].id,
                     "institution_id": world.institutions["A"], "outcome": "success", "correlation_id": "corr-upload-1"}
    assert [a for a in audits(db_conn) if a["action"] == "evidence.download_url"] == []


def test_extended_retention_is_180_days(upload_app, world, edata, as_user, db_conn):
    session = edata.sessions["a1"]
    db_conn.execute(text("UPDATE work_session SET extended_retention = TRUE WHERE id = :s"), {"s": session.id})
    client, _ = upload_app()
    body = _post(client, as_user("op_a"), _form(session.id)).get_json()
    [row] = [r for r in _assets(db_conn, session.id) if str(r["id"]) == body["id"]]
    assert row["retention_until"] - row["created_at"] == timedelta(days=180)


def test_client_filename_and_declared_type_are_ignored(upload_app, world, edata, as_user, db_conn):
    client, storage = upload_app()
    session = edata.sessions["a1"]
    response = _post(client, as_user("op_a"), _form(session.id, filename="../../etc/secret.gif", content_type="text/plain"))
    assert response.status_code == 201 and response.get_json()["content_type"] == "image/jpeg"
    [key] = storage.objects
    assert key == f"{world.institutions['A']}/{session.id}/{datetime.now(timezone.utc).year}/{response.get_json()['id']}.jpg"
    assert ".." not in key and "secret" not in key
    row = db_conn.execute(text("SELECT * FROM media_asset WHERE id = :i"), {"i": response.get_json()["id"]}).mappings().one()
    assert "secret" not in " ".join(str(v) for v in row.values())


def test_double_upload_creates_two_assets(upload_app, world, edata, as_user, db_conn):
    """No idempotency key and no SHA dedupe in v1."""
    client, storage = upload_app()
    session = edata.sessions["a1"]
    first = _post(client, as_user("op_a"), _form(session.id)).get_json()
    second = _post(client, as_user("op_a"), _form(session.id)).get_json()
    assert first["id"] != second["id"] and first["sha256"] == second["sha256"]
    assert len(storage.objects) == 2 and len(_upload_audits(db_conn)) == 2


def test_uploaded_asset_appears_in_reads(upload_app, world, edata, as_user, db_conn):
    client, storage = upload_app()
    session = edata.sessions["empty"]
    asset_id = _post(client, as_user("op_a"), _form(session.id)).get_json()["id"]
    listed = client.get(f"/api/v1/sessions/{session.id}/evidence", headers=as_user("op_a")).get_json()
    assert [i["id"] for i in listed] == [asset_id] and listed[0]["signed_url"] is None
    detail = client.get(f"/api/v1/evidence/{asset_id}", headers=as_user("spd_a")).get_json()
    assert detail["signed_url"] is not None
    assert [a["action"] for a in audits(db_conn, uuid.UUID(asset_id))] == ["evidence.upload", "evidence.download_url"]


def test_xml_response(upload_app, world, edata, as_user):
    client, _ = upload_app()
    response = _post(client, as_user("op_a", Accept="application/xml"), _form(edata.sessions["a1"].id))
    root = ET.fromstring(response.data)
    assert response.status_code == 201 and root.tag == "evidence" and [c.tag for c in root] == FIELDS
    assert root.find("signed_url").get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true"


# --- Roles, tenant, ownership ---------------------------------------------------------------------------


@pytest.mark.parametrize("user_key, session_key, status", [
    ("op_a2", "a1", 404),       # same tenant, not assigned
    ("op_b", "a1", 404),        # other tenant
    ("op_a", "b1", 404),        # session of another tenant
    ("op_a", "noop", 404),      # session without operation
    ("spd_a", "a1", 403),       # supervisor only: upload needs station_operator
    ("admin_a", "a1", 403),
    ("viewer_a", "a1", 403),
    ("multi_a", "a1", 404),     # station_operator + spd_supervisor, but not the assigned operator
])
def test_access_rules(upload_app, world, edata, as_user, db_conn, user_key, session_key, status):
    client, storage = upload_app()
    session = edata.sessions[session_key]
    before = len(_assets(db_conn, session.id))
    response = _post(client, as_user(user_key), _form(session.id))
    assert response.status_code == status
    if status == 404:
        assert response.get_json()["message"] == "Session not found"
    assert len(_assets(db_conn, session.id)) == before and _upload_audits(db_conn) == []
    assert storage.calls == [] and storage.objects == {}


def test_multi_role_assigned_operator_can_upload(upload_app, world, edata, as_user, db_conn):
    session = edata.sessions["a1"]
    db_conn.execute(text("UPDATE work_session SET user_id = :u WHERE id = :s"), {"u": world.users["multi_a"].id, "s": session.id})
    client, _ = upload_app()
    assert _post(client, as_user("multi_a"), _form(session.id)).status_code == 201


def test_unknown_session_is_404(upload_app, world, edata, as_user):
    client, storage = upload_app()
    assert _post(client, as_user("op_a"), _form(uuid.uuid4())).status_code == 404 and storage.calls == []


# --- Privacy and session state ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["in_progress", "correction_required", "awaiting_spd_review"])
def test_allowed_states(upload_app, world, edata, as_user, db_conn, status):
    session = edata.sessions["a1"]
    db_conn.execute(text("UPDATE work_session SET status_id = (SELECT id FROM cat_session_status WHERE code = :c) "
                         "WHERE id = :s"), {"c": status, "s": session.id})
    client, _ = upload_app()
    assert _post(client, as_user("op_a"), _form(session.id)).status_code == 201


@pytest.mark.parametrize("status", ["scheduled", "closed", "aborted"])
def test_rejected_states(upload_app, world, edata, as_user, db_conn, status):
    session = edata.sessions["a1"]
    before = len(_assets(db_conn, session.id))
    db_conn.execute(text("UPDATE work_session SET status_id = (SELECT id FROM cat_session_status WHERE code = :c) "
                         "WHERE id = :s"), {"c": status, "s": session.id})
    client, storage = upload_app()
    response = _post(client, as_user("op_a"), _form(session.id))
    assert response.status_code == 409 and response.get_json()["details"] == {"status_code": status}
    assert len(_assets(db_conn, session.id)) == before and storage.calls == [] and _upload_audits(db_conn) == []


@pytest.mark.parametrize("capture_mode", ["manual_no_privacy", None])
def test_non_vision_session_is_409(upload_app, world, edata, as_user, db_conn, capture_mode):
    session = edata.sessions["empty"]
    db_conn.execute(text("UPDATE work_session SET capture_mode = :m WHERE id = :s"), {"m": capture_mode, "s": session.id})
    client, storage = upload_app()
    response = _post(client, as_user("op_a"), _form(session.id))
    assert response.status_code == 409 and response.get_json()["details"] == {"capture_mode": capture_mode}
    _assert_nothing_persisted(db_conn, storage, session.id)


# --- Multipart contract ------------------------------------------------------------------------------------


def _bad(session_id, case):
    form = _form(session_id)
    if case.startswith("missing_"):
        form.pop(case.removeprefix("missing_"))
    elif case == "two_session_id":
        form["session_id"] = [str(session_id), str(session_id)]
    elif case == "two_kind":
        form["kind"] = ["jpeg", "jpeg"]
    elif case == "two_file":
        form["file"] = [(io.BytesIO(JPEG), "a.jpg"), (io.BytesIO(JPEG), "b.jpg")]
    elif case == "unknown_text":
        form["comment"] = "x"
    elif case == "unknown_file":
        form["thumbnail"] = (io.BytesIO(JPEG), "t.jpg")
    elif case == "file_as_text":
        form["file"] = "not a file"
    elif case == "session_as_file":
        form["session_id"] = (io.BytesIO(str(session_id).encode()), "s.txt")
    else:
        form[case] = "x"   # client-supplied server-owned field
    return form


@pytest.mark.parametrize("case", [
    "missing_session_id", "missing_kind", "missing_file", "two_session_id", "two_kind", "two_file",
    "unknown_text", "unknown_file", "file_as_text", "session_as_file",
    "reason", "institution_id", "object_key", "bucket", "storage_provider", "sha256", "size_bytes",
    "retention_until", "created_at", "capture_mode", "user_id",
])
def test_invalid_parts_are_400(upload_app, world, edata, as_user, db_conn, tempfiles, case):
    client, storage = upload_app()
    session = edata.sessions["a1"]
    before = len(_assets(db_conn, session.id))
    response = _post(client, as_user("op_a"), _bad(session.id, case))
    assert response.status_code == 400, response.get_data(as_text=True)
    assert response.get_json()["error"] == "invalid_request"
    assert storage.calls == [] and len(_assets(db_conn, session.id)) == before and _upload_audits(db_conn) == []
    assert all(handle.closed for handle in tempfiles)


@pytest.mark.parametrize("kind", ["weights", "other", "JPEG", "png", ""])
def test_invalid_kind_is_400(upload_app, world, edata, as_user, kind):
    client, storage = upload_app()
    response = _post(client, as_user("op_a"), _form(edata.sessions["a1"].id, kind=kind))
    assert response.status_code == 400 and response.get_json()["details"]["field"] == "kind" and storage.calls == []


def test_malformed_session_id_is_400(upload_app, world, edata, as_user):
    client, _ = upload_app()
    response = _post(client, as_user("op_a"), _form("not-a-uuid"))
    assert response.status_code == 400 and response.get_json()["details"] == {"field": "session_id"}


@pytest.mark.parametrize("content_type, data", [("application/json", b'{"session_id": "x"}'),
                                                ("application/octet-stream", JPEG),
                                                ("application/xml", b"<evidence/>")])
def test_non_multipart_is_400(upload_app, world, edata, as_user, content_type, data):
    client, storage = upload_app()
    response = client.post(URL, data=data, content_type=content_type, headers=as_user("op_a"))
    assert response.status_code == 400 and response.get_json()["details"] == {"content_type": content_type}
    assert storage.calls == []


# --- Content validation ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind, data", [("jpeg", GIF89), ("gif", JPEG), ("jpeg", b"this is not an image" * 10),
                                        ("gif", b"GIF88a" + b"x" * 20), ("jpeg", b"\xff\xd8" + b"x" * 20)])
def test_content_not_matching_kind_is_422(upload_app, world, edata, as_user, db_conn, tempfiles, kind, data):
    client, storage = upload_app()
    session = edata.sessions["empty"]
    response = _post(client, as_user("op_a"), _form(session.id, kind, data, filename="photo.jpg"))
    assert response.status_code == 422 and response.get_json()["error"] == "validation_error"
    _assert_nothing_persisted(db_conn, storage, session.id)
    assert all(handle.closed for handle in tempfiles)


def test_empty_file_is_400(upload_app, world, edata, as_user, db_conn):
    client, storage = upload_app()
    response = _post(client, as_user("op_a"), _form(edata.sessions["empty"].id, data=b""))
    assert response.status_code == 400 and response.get_json()["details"] == {"field": "file"}
    _assert_nothing_persisted(db_conn, storage, edata.sessions["empty"].id)


@pytest.mark.parametrize("kind, header, setting", [("jpeg", b"\xff\xd8\xff\xe0", "jpeg_max_bytes"),
                                                   ("gif", b"GIF89a", "gif_max_bytes")])
def test_size_limits_are_exact(upload_app, world, edata, as_user, db_conn, kind, header, setting):
    client, storage = upload_app(**{setting: 4096})
    session = edata.sessions["empty"]
    at_limit = header + b"\x00" * (4096 - len(header))
    assert _post(client, as_user("op_a"), _form(session.id, kind, at_limit)).status_code == 201
    response = _post(client, as_user("op_a"), _form(session.id, kind, at_limit + b"\x00"))
    assert response.status_code == 413 and response.get_json()["error"] == "payload_too_large"
    assert response.get_json()["details"] == {"field": "file", "kind": kind, "max_bytes": 4096}
    assert len(storage.objects) == 1 and len(_assets(db_conn, session.id)) == 1     # nothing for the oversized one


def test_default_jpeg_limit(upload_app, world, edata, as_user):
    client, storage = upload_app()
    session = edata.sessions["empty"]
    limit = 2 * 1024 * 1024
    big = b"\xff\xd8\xff\xe0" + b"\x00" * (limit - 4)
    assert _post(client, as_user("op_a"), _form(session.id, "jpeg", big)).status_code == 201
    assert _post(client, as_user("op_a"), _form(session.id, "jpeg", big + b"\x00")).status_code == 413
    assert len(storage.objects) == 1


def test_gif_bigger_than_jpeg_limit_is_accepted_up_to_its_own(upload_app, world, edata, as_user):
    client, _ = upload_app()
    gif = b"GIF89a" + b"\x00" * (3 * 1024 * 1024)
    assert _post(client, as_user("op_a"), _form(edata.sessions["empty"].id, "gif", gif)).status_code == 201


def test_request_envelope_limit_is_413(upload_app, world, edata, as_user):
    client, storage = upload_app(jpeg_max_bytes=1024, gif_max_bytes=1024)   # envelope = 1 KiB + 64 KiB
    response = _post(client, as_user("op_a"), _form(edata.sessions["a1"].id, "jpeg", b"\xff\xd8\xff" + b"\x00" * 70000))
    assert response.status_code == 413 and storage.calls == []


def test_sha256_matches_an_independent_hash(upload_app, world, edata, as_user, db_conn):
    client, _ = upload_app()
    data = b"\xff\xd8\xff\xdb" + bytes(range(256)) * 300
    body = _post(client, as_user("op_a"), _form(edata.sessions["empty"].id, "jpeg", data)).get_json()
    assert body["sha256"] == hashlib.sha256(data).hexdigest() and body["sha256"] == body["sha256"].lower()
    assert len(body["sha256"]) == 64


# --- Storage / database failures and compensation ---------------------------------------------------------------


def _orphan_log(monkeypatch):
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())
    handler = Capture()
    logger = logging.getLogger("evidence.upload")
    logger.addHandler(handler)
    monkeypatch.setattr(logger, "level", logging.INFO)
    return records


def test_put_failure_is_503_without_db(upload_app, world, edata, as_user, db_conn, tempfiles):
    client, storage = upload_app(FakeStorage(put_error=True))
    session = edata.sessions["empty"]
    response = _post(client, as_user("op_a"), _form(session.id))
    assert response.status_code == 503 and response.get_json()["details"] == {"dependency": "storage"}
    assert [c.split(":")[0] for c in storage.calls] == ["put"]                       # no head, no delete, no DB
    _assert_nothing_persisted(db_conn, storage, session.id)
    assert all(handle.closed for handle in tempfiles)


@pytest.mark.parametrize("storage_kwargs", [{"head_error": True}, {"size_delta": 1}, {"size_delta": -1}])
def test_head_failure_or_size_mismatch_deletes_the_object(upload_app, world, edata, as_user, db_conn, storage_kwargs):
    client, storage = upload_app(FakeStorage(**storage_kwargs))
    session = edata.sessions["empty"]
    response = _post(client, as_user("op_a"), _form(session.id))
    assert response.status_code == 503 and response.get_json()["details"] == {"dependency": "storage"}
    assert [c.split(":")[0] for c in storage.calls] == ["put", "head", "delete"]
    _assert_nothing_persisted(db_conn, storage, session.id)


@pytest.mark.parametrize("target", ["insert", "audit_constraint", "audit_unexpected"])
def test_db_failure_rolls_back_and_deletes_the_object(upload_app, world, edata, as_user, db_conn, monkeypatch,
                                                      tempfiles, target):
    from adapters import audit_adapter
    from repositories import evidence_repository

    if target == "insert":
        def boom(*args, **kwargs):
            raise RuntimeError("INSERT INTO media_asset failed with secret detail")
        monkeypatch.setattr(evidence_repository, "insert_media_asset", boom)
    elif target == "audit_constraint":
        monkeypatch.setattr(audit_adapter, "ACTOR_TYPE", "robot")     # access_audit CHECK violation
    else:
        def explode(*args, **kwargs):
            raise RuntimeError("audit exploded")
        monkeypatch.setattr(audit_adapter, "record", explode)
    client, storage = upload_app()
    session = edata.sessions["empty"]
    response = _post(client, as_user("op_a"), _form(session.id))
    assert response.status_code == 500 and response.get_json()["error"] == "internal_error"
    assert "INSERT" not in response.get_data(as_text=True) and "secret" not in response.get_data(as_text=True)
    assert [c.split(":")[0] for c in storage.calls] == ["put", "head", "delete"]
    _assert_nothing_persisted(db_conn, storage, session.id)                          # no row, no audit, no blob
    assert all(handle.closed for handle in tempfiles)


def test_compensation_failure_keeps_error_and_logs_orphan(upload_app, world, edata, as_user, db_conn, monkeypatch):
    from adapters import audit_adapter

    monkeypatch.setattr(audit_adapter, "ACTOR_TYPE", "robot")
    records = _orphan_log(monkeypatch)
    client, storage = upload_app(FakeStorage(delete_error=True))
    session = edata.sessions["empty"]
    response = _post(client, as_user("op_a", **{"X-Correlation-ID": "corr-orphan"}), _form(session.id))
    assert response.status_code == 500 and response.get_json()["error"] == "internal_error"
    [key] = storage.objects                                                          # the blob stayed behind
    orphan = [m for m in records if "orphan candidate" in m]
    assert orphan and key in orphan[0] and "bucket=evidence" in orphan[0]
    assert key not in response.get_data(as_text=True)
    assert _assets(db_conn, session.id) == [] and _upload_audits(db_conn) == []      # no invented row


def test_session_closed_during_put_is_409_and_object_deleted(upload_app, world, edata, as_user, db_conn):
    """Coordinated race on the same connection; tests/test_upload_concurrency.py repeats it on real locks."""
    session = edata.sessions["empty"]

    def close_session():
        db_conn.execute(text("UPDATE work_session SET status_id = (SELECT id FROM cat_session_status "
                             "WHERE code = 'closed') WHERE id = :s"), {"s": session.id})
    client, storage = upload_app(FakeStorage(on_put=close_session))
    response = _post(client, as_user("op_a"), _form(session.id))
    assert response.status_code == 409 and response.get_json()["details"] == {"status_code": "closed"}
    assert [c.split(":")[0] for c in storage.calls] == ["put", "head", "delete"]
    _assert_nothing_persisted(db_conn, storage, session.id)


def test_success_closes_the_temporary_file(upload_app, world, edata, as_user, tempfiles):
    client, _ = upload_app()
    assert _post(client, as_user("op_a"), _form(edata.sessions["a1"].id)).status_code == 201
    assert len(tempfiles) == 1 and tempfiles[0].closed


# --- Correlation ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("incoming, preserved", [("u" * 64, True), ("u" * 65, False)])
def test_upload_audit_correlation_equals_header(upload_app, world, edata, as_user, db_conn, incoming, preserved):
    client, _ = upload_app()
    response = _post(client, as_user("op_a", **{"X-Correlation-ID": incoming}), _form(edata.sessions["a1"].id))
    answered = response.headers["X-Correlation-ID"]
    assert (answered == incoming) is preserved
    assert [a["correlation_id"] for a in _upload_audits(db_conn)] == [answered]


def test_upload_error_correlation_json_and_xml(upload_app, world, edata, as_user):
    client, _ = upload_app()
    response = client.post(URL, data=_form(edata.sessions["b1"].id), content_type="multipart/form-data",
                           headers=as_user("op_a", Accept="application/xml"),
                           environ_overrides={"HTTP_X_CORRELATION_ID": "abc\n"})
    answered = response.headers["X-Correlation-ID"]
    root = ET.fromstring(response.data)
    assert response.status_code == 404 and str(uuid.UUID(answered)) == answered
    assert root.tag == "error_response" and root.findtext("correlation_id") == answered


def test_upload_requires_token(upload_app, world, edata):
    client, storage = upload_app()
    response = _post(client, {}, _form(edata.sessions["a1"].id))
    assert response.status_code == 401 and storage.calls == []
