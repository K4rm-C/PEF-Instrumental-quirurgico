"""HTTP bridge from BackendWebFlask to the VisionWorker process."""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

import requests
from sqlalchemy import select

from extensions import db
from models.CatInstrumentCategory import CatInstrumentCategory
from models.ExpectedInventory import ExpectedInventory
from models.InstrumentFamily import InstrumentFamily
from models.ModelClass import ModelClass
from models.YoloModel import YoloModel

VISION_WORKER_URL = os.getenv("VISION_WORKER_URL", "http://127.0.0.1:5002").rstrip("/")
VISION_WORKER_TIMEOUT = float(os.getenv("VISION_WORKER_TIMEOUT", "30"))
CONF_DISPLAY_THRESHOLD = float(os.getenv("VISION_CONF_DISPLAY_THRESHOLD", "0.70"))


class VisionBridgeError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def worker_health() -> dict[str, Any]:
    try:
        resp = requests.get(f"{VISION_WORKER_URL}/health", timeout=min(VISION_WORKER_TIMEOUT, 5))
        if resp.status_code >= 400:
            return {"ok": False, "error": f"Worker HTTP {resp.status_code}"}
        return resp.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}


def active_model_payload() -> dict[str, Any]:
    model = db.session.scalar(select(YoloModel).where(YoloModel.active.is_(True)))
    if model is None:
        return {
            "model_version": "yolo26l-demo",
            "model_id": None,
            "conf_threshold": CONF_DISPLAY_THRESHOLD,
            "class_map": [],
        }
    rows = db.session.execute(
        select(ModelClass, InstrumentFamily, CatInstrumentCategory)
        .join(InstrumentFamily, InstrumentFamily.id == ModelClass.family_id)
        .join(CatInstrumentCategory, CatInstrumentCategory.id == InstrumentFamily.category_id)
        .where(ModelClass.model_id == model.id)
    ).all()
    class_map = [
        {
            "yolo_class_id": mc.yolo_class_id,
            "family_id": str(family.id),
            "family_code": family.code,
            "family_name": family.name,
            "category_name": category.name,
        }
        for mc, family, category in rows
    ]
    return {
        "model_version": model.version_tag,
        "model_id": str(model.id),
        "conf_threshold": CONF_DISPLAY_THRESHOLD,
        "class_map": class_map,
    }


def expected_payload_for_session(session_id: UUID) -> list[dict[str, Any]]:
    rows = db.session.execute(
        select(ExpectedInventory, InstrumentFamily, CatInstrumentCategory)
        .join(InstrumentFamily, InstrumentFamily.id == ExpectedInventory.family_id)
        .join(CatInstrumentCategory, CatInstrumentCategory.id == InstrumentFamily.category_id)
        .where(ExpectedInventory.session_id == session_id)
        .order_by(InstrumentFamily.name)
    ).all()
    return [
        {
            "family_id": str(family.id),
            "family_code": family.code,
            "family_name": family.name,
            "category_name": category.name,
            "expected_quantity": inv.expected_quantity,
        }
        for inv, family, category in rows
    ]


def bind_session(session_id: UUID) -> dict[str, Any]:
    model = active_model_payload()
    body = {
        "expected": expected_payload_for_session(session_id),
        "class_map": model["class_map"],
        "model_version": model["model_version"],
        "conf_threshold": model["conf_threshold"],
    }
    try:
        resp = requests.post(
            f"{VISION_WORKER_URL}/sessions/{session_id}/bind",
            json=body,
            timeout=VISION_WORKER_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise VisionBridgeError(f"Vision worker unreachable: {exc}", 503) from exc
    if resp.status_code >= 400:
        raise VisionBridgeError(f"Vision bind failed: {resp.text}", resp.status_code)
    return resp.json()


def unbind_session(session_id: UUID) -> dict[str, Any]:
    try:
        resp = requests.post(
            f"{VISION_WORKER_URL}/sessions/{session_id}/unbind",
            timeout=min(VISION_WORKER_TIMEOUT, 8),
        )
        if resp.status_code >= 400:
            return {"ok": False, "error": resp.text}
        return resp.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}


def fetch_state(session_id: UUID) -> dict[str, Any]:
    try:
        resp = requests.get(
            f"{VISION_WORKER_URL}/sessions/{session_id}/state",
            timeout=min(VISION_WORKER_TIMEOUT, 8),
        )
    except requests.RequestException as exc:
        raise VisionBridgeError(f"Vision worker unreachable: {exc}", 503) from exc
    if resp.status_code == 404:
        # Auto-rebind if worker restarted but session still in progress.
        bind_session(session_id)
        resp = requests.get(
            f"{VISION_WORKER_URL}/sessions/{session_id}/state",
            timeout=min(VISION_WORKER_TIMEOUT, 8),
        )
    if resp.status_code >= 400:
        raise VisionBridgeError(f"Vision state failed: {resp.text}", resp.status_code)
    return resp.json()


def fetch_timeline(session_id: UUID, run_id: str | None = None) -> dict[str, Any]:
    """Sampled tallies written by the worker during video processing."""
    params = {}
    if run_id:
        params["run_id"] = run_id
    try:
        resp = requests.get(
            f"{VISION_WORKER_URL}/sessions/{session_id}/timeline",
            params=params,
            timeout=min(VISION_WORKER_TIMEOUT, 15),
        )
    except requests.RequestException as exc:
        raise VisionBridgeError(f"Vision worker unreachable: {exc}", 503) from exc
    if resp.status_code >= 400:
        raise VisionBridgeError(f"Vision timeline failed: {resp.text}", resp.status_code)
    return resp.json()


def upload_video(session_id: UUID, file_storage) -> dict[str, Any]:
    try:
        files = {
            "video": (
                file_storage.filename,
                file_storage.stream,
                file_storage.mimetype or "application/octet-stream",
            )
        }
        resp = requests.post(
            f"{VISION_WORKER_URL}/sessions/{session_id}/process-video",
            files=files,
            timeout=max(VISION_WORKER_TIMEOUT, 120),
        )
    except requests.RequestException as exc:
        raise VisionBridgeError(f"Vision upload failed: {exc}", 503) from exc
    if resp.status_code >= 400:
        detail = resp.json().get("error") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        raise VisionBridgeError(detail or "Vision upload failed", resp.status_code)
    return resp.json()
