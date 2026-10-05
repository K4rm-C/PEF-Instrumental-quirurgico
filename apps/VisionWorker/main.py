"""PEF Vision Worker — binds to OP vision sessions, runs YOLO on uploaded video."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, request

from config import CONF_DISPLAY_THRESHOLD, HOST, PORT, UPLOAD_DIR, WEIGHTS_PATH
from inference import SessionState, get_model, process_video_file

app = Flask(__name__)

_sessions: dict[str, SessionState] = {}
_lock = threading.Lock()
_jobs: dict[str, threading.Thread] = {}


def _get_state(session_id: str) -> SessionState | None:
    with _lock:
        return _sessions.get(session_id)


@app.get("/health")
def health():
    weights_ok = WEIGHTS_PATH.is_file()
    return jsonify({
        "ok": True,
        "weights_path": str(WEIGHTS_PATH),
        "weights_present": weights_ok,
        "bound_sessions": len(_sessions),
        "conf_display_threshold": CONF_DISPLAY_THRESHOLD,
    })


@app.post("/sessions/<session_id>/bind")
def bind_session(session_id: str):
    payload = request.get_json(silent=True) or {}
    expected_list = payload.get("expected") or []
    class_map_list = payload.get("class_map") or []
    expected = {}
    for row in expected_list:
        code = str(row.get("family_code") or "").strip()
        if not code:
            continue
        expected[code] = {
            "family_id": row.get("family_id"),
            "family_code": code,
            "family_name": row.get("family_name") or code,
            "category_name": row.get("category_name") or "Type",
            "expected_quantity": int(row.get("expected_quantity") or 0),
        }
    class_map = {}
    for row in class_map_list:
        try:
            yolo_id = int(row["yolo_class_id"])
        except (KeyError, TypeError, ValueError):
            continue
        class_map[yolo_id] = {
            "family_id": row.get("family_id"),
            "family_code": row.get("family_code"),
            "family_name": row.get("family_name"),
            "category_name": row.get("category_name") or "Type",
        }
    with _lock:
        prev = _sessions.get(session_id)
        state = prev or SessionState(session_id=session_id)
        state.expected = expected
        state.class_map = class_map
        state.model_version = str(payload.get("model_version") or state.model_version)
        state.conf_threshold = float(payload.get("conf_threshold") or CONF_DISPLAY_THRESHOLD)
        if state.status not in {"processing"}:
            state.status = "idle"
            state.message = "Bound; waiting for video"
        _sessions[session_id] = state
    # Warm model in background so first upload is faster.
    threading.Thread(target=lambda: get_model(), daemon=True).start()
    return jsonify({"ok": True, "session": state.to_public()})


@app.post("/sessions/<session_id>/unbind")
def unbind_session(session_id: str):
    with _lock:
        state = _sessions.pop(session_id, None)
        if state:
            state.cancel = True
    return jsonify({"ok": True, "unbound": bool(state)})


@app.get("/sessions/<session_id>/state")
def session_state(session_id: str):
    state = _get_state(session_id)
    if state is None:
        return jsonify({"ok": False, "error": "Session not bound"}), 404
    return jsonify({"ok": True, "session": state.to_public()})


@app.post("/sessions/<session_id>/process-video")
def process_video(session_id: str):
    state = _get_state(session_id)
    if state is None:
        return jsonify({"ok": False, "error": "Session not bound. Start a vision session first."}), 409
    if state.status == "processing":
        return jsonify({"ok": False, "error": "Already processing a video for this session."}), 409

    upload = request.files.get("video")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "Missing video file field 'video'."}), 400

    suffix = Path(upload.filename).suffix.lower() or ".mp4"
    if suffix not in {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"}:
        return jsonify({
            "ok": False,
            "error": f"Unsupported format {suffix}. Use mp4, mkv, avi, mov, webm or m4v.",
        }), 400

    job_id = uuid.uuid4().hex
    dest = UPLOAD_DIR / f"{session_id}_{job_id}{suffix}"
    upload.save(dest)

    def _run():
        try:
            process_video_file(str(dest), state)
        finally:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass

    thread = threading.Thread(target=_run, daemon=True, name=f"yolo-{session_id[:8]}")
    with _lock:
        _jobs[session_id] = thread
    thread.start()
    return jsonify({"ok": True, "job_id": job_id, "session": state.to_public()}), 202


@app.post("/sessions/<session_id>/cancel")
def cancel_job(session_id: str):
    state = _get_state(session_id)
    if state is None:
        return jsonify({"ok": False, "error": "Session not bound"}), 404
    state.cancel = True
    return jsonify({"ok": True})


if __name__ == "__main__":
    print(f"VisionWorker on http://{HOST}:{PORT}")
    print(f"Weights: {WEIGHTS_PATH} (present={WEIGHTS_PATH.is_file()})")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
