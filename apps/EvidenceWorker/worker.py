"""EvidenceWorker loop: Redis frames → RAM ring; triggers → clip assemble; optional Garage."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from pathlib import Path

from assembler import (
    _write_sidecar,
    append_live_frame,
    fallback_jpeg,
    finalize_clip,
    purge_local_media,
    start_clip_job,
)
from config import (
    FRAME_LIST_PREFIX,
    FRAME_POLL_MS,
    GARAGE_ENABLED,
    HOURLY_SECONDS,
    IDLE_GAP_SECONDS,
    PURGE_LOCAL_ON_UPLOAD,
    RING_SECONDS,
    TRIGGER_BLOCK_S,
    TRIGGER_LIST_KEY,
    UPLOAD_RETRY_POLL_S,
    REDIS_URL,
)
from garage import publish_clip_to_garage
from ring_buffer import FrameItem, RingRegistry, decode_jpeg_b64
from upload_retry import enqueue_upload_retry, pop_due_retries, requeue_with_backoff
from vision_snapshot import fetch_session_ratio

logger = logging.getLogger(__name__)


class EvidenceWorker:
    def __init__(self) -> None:
        self.rings = RingRegistry(max_seconds=RING_SECONDS)
        self._active_clips: dict[str, Any] = {}
        self._clip_lock = threading.Lock()
        self._stop = threading.Event()
        self._redis = None
        self._last_retry_poll = 0.0

    def _client(self):
        if self._redis is not None:
            return self._redis
        import redis
        self._redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self._redis.ping()
        return self._redis

    def stop(self) -> None:
        self._stop.set()

    def _apply_trigger_meta(self, ring, payload: dict[str, Any]) -> None:
        phase_id = payload.get("phase_id")
        if phase_id:
            ring.phase_id = str(phase_id)
        extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
        if extra.get("phase_code"):
            ring.phase_code = str(extra["phase_code"])
        if extra.get("phase_name"):
            ring.phase_name = str(extra["phase_name"])
        if extra.get("ratio_label"):
            ring.last_ratio_label = str(extra["ratio_label"])

    def _ingest_frame_payload(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        sid = str(msg.get("session_id") or "")
        jpeg = decode_jpeg_b64(str(msg.get("jpeg_b64") or ""))
        if not sid or jpeg is None:
            return
        ts = float(msg.get("ts") or time.time())
        ring = self.rings.get(sid)
        ring.push(jpeg, ts=ts)
        item = FrameItem(ts=ts, jpeg=jpeg)
        with self._clip_lock:
            job = self._active_clips.get(sid)
            if job and not job.done:
                append_live_frame(job, item)
        # Catch-up hourly when frames resume after idle.
        if ring.pending_hourly:
            self._maybe_hourly(ring, catch_up=True)

    def _drain_session_frames(self, session_id: str, client) -> int:
        key = f"{FRAME_LIST_PREFIX}{session_id}"
        n = 0
        while True:
            raw = client.rpop(key)
            if raw is None:
                break
            self._ingest_frame_payload(raw)
            n += 1
            if n >= 200:
                break
        return n

    def _drain_known_frames(self, client) -> None:
        # Prefer known sessions; also scan a few keys if idle.
        for ring in self.rings.all_sessions():
            self._drain_session_frames(ring.session_id, client)
        try:
            for key in client.scan_iter(match=f"{FRAME_LIST_PREFIX}*", count=20):
                sid = str(key).split(FRAME_LIST_PREFIX, 1)[-1]
                if sid:
                    self._drain_session_frames(sid, client)
        except Exception as exc:  # noqa: BLE001
            logger.debug("frame scan: %s", exc)

    def _handle_trigger(self, payload: dict[str, Any]) -> None:
        sid = str(payload.get("session_id") or "")
        reason = str(payload.get("reason") or "").strip().lower()
        if not sid or reason not in {"start", "phase", "close", "hourly"}:
            return
        phase_id = payload.get("phase_id")
        occurred_at = payload.get("occurred_at")
        extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
        ring = self.rings.get(sid)
        self._apply_trigger_meta(ring, payload)
        with self._clip_lock:
            if sid in self._active_clips and not self._active_clips[sid].done:
                logger.info("trigger %s ignored; clip already recording for %s", reason, sid)
                return
            job = start_clip_job(
                ring,
                reason=reason,
                phase_id=str(phase_id) if phase_id else None,
                occurred_at=str(occurred_at) if occurred_at else None,
                phase_code=str(extra["phase_code"]) if extra.get("phase_code") else None,
                phase_name=str(extra["phase_name"]) if extra.get("phase_name") else None,
                ratio_label=str(extra["ratio_label"]) if extra.get("ratio_label") else None,
                detected_total=int(extra["detected_total"]) if extra.get("detected_total") is not None else None,
                expected_total=int(extra["expected_total"]) if extra.get("expected_total") is not None else None,
            )
            self._active_clips[sid] = job
            logger.info(
                "clip armed session=%s reason=%s pre_frames=%s post_until=%.3f ratio=%s phase=%s",
                sid, reason, len(job.frames), job.post_until, job.ratio_label, job.phase_code,
            )

    def _finalize_due_clips(self) -> None:
        now = time.time()
        due: list[str] = []
        with self._clip_lock:
            for sid, job in self._active_clips.items():
                if not job.done and now >= job.post_until:
                    due.append(sid)
        for sid in due:
            with self._clip_lock:
                job = self._active_clips.get(sid)
            if job is None or job.done:
                continue
            try:
                meta = finalize_clip(job)
            except Exception as exc:  # noqa: BLE001
                logger.warning("video encode failed, jpeg fallback: %s", exc)
                try:
                    meta = fallback_jpeg(job)
                except Exception as exc2:  # noqa: BLE001
                    logger.error("evidence clip failed entirely: %s", exc2)
                    with self._clip_lock:
                        self._active_clips.pop(sid, None)
                    continue
            self.rings.get(sid).mark_saved()
            self.rings.get(sid).pending_hourly = False
            self._publish_or_queue(meta)
            with self._clip_lock:
                self._active_clips.pop(sid, None)

    def _publish_or_queue(self, meta: dict[str, Any]) -> None:
        """BD-first path: upload S3+PG+Mongo; on fail keep local + retry queue."""
        if not GARAGE_ENABLED:
            meta["upload_status"] = "local_only"
            try:
                _write_sidecar(meta, Path(meta["local_path"]))
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            garage_meta = publish_clip_to_garage(meta)
        except Exception as exc:  # noqa: BLE001
            logger.warning("garage soft-fail session=%s: %s", meta.get("session_id"), exc)
            garage_meta = {"garage_ok": False, "error": str(exc)[:240]}
        meta["garage"] = garage_meta
        if garage_meta.get("garage_ok") and garage_meta.get("mongo_ok"):
            meta["upload_status"] = "uploaded"
            try:
                _write_sidecar(meta, Path(meta["local_path"]))
            except Exception:  # noqa: BLE001
                pass
            if PURGE_LOCAL_ON_UPLOAD:
                try:
                    purge_local_media(meta)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("purge after upload failed: %s", exc)
            return
        meta["upload_status"] = "pending_upload"
        logger.warning(
            "garage soft-fail session=%s err=%s (local kept; queued retry)",
            meta.get("session_id"), garage_meta.get("error"),
        )
        try:
            _write_sidecar(meta, Path(meta["local_path"]))
        except Exception:  # noqa: BLE001
            pass
        enqueue_upload_retry(meta, error=str(garage_meta.get("error") or "upload_failed"))

    def _process_upload_retries(self) -> None:
        now = time.time()
        if now - self._last_retry_poll < UPLOAD_RETRY_POLL_S:
            return
        self._last_retry_poll = now
        if not GARAGE_ENABLED:
            return
        for item in pop_due_retries(limit=3):
            local = Path(str(item.get("local_path") or ""))
            if not local.is_file():
                logger.warning(
                    "retry skipped missing file clip=%s path=%s",
                    item.get("clip_id"), local,
                )
                continue
            meta = dict(item)
            meta["path"] = str(local)
            meta["local_path"] = str(local)
            try:
                garage_meta = publish_clip_to_garage(meta)
            except Exception as exc:  # noqa: BLE001
                requeue_with_backoff(item, error=str(exc))
                continue
            meta["garage"] = garage_meta
            if garage_meta.get("garage_ok") and garage_meta.get("mongo_ok"):
                meta["upload_status"] = "uploaded"
                try:
                    _write_sidecar(meta, local)
                except Exception:  # noqa: BLE001
                    pass
                if PURGE_LOCAL_ON_UPLOAD:
                    try:
                        purge_local_media(meta)
                    except Exception:  # noqa: BLE001
                        pass
                logger.info(
                    "upload retry OK clip=%s session=%s",
                    meta.get("clip_id"), meta.get("session_id"),
                )
            else:
                requeue_with_backoff(item, error=str(garage_meta.get("error") or "retry_failed"))

    def _maybe_hourly(self, ring, *, catch_up: bool = False) -> None:
        """Fire hourly only while frames are active; mark pending while idle if overdue."""
        now = time.time()
        overdue = (now - float(ring.last_saved_at)) >= HOURLY_SECONDS
        if not overdue:
            ring.pending_hourly = False
            return
        last_frame = float(ring.last_frame_at or 0)
        active = last_frame > 0 and (now - last_frame) <= IDLE_GAP_SECONDS
        if not active:
            # Idle: never fire; remember we owe a catch-up when frames resume.
            ring.pending_hourly = True
            return
        with self._clip_lock:
            busy = bool(
                ring.session_id in self._active_clips
                and not self._active_clips[ring.session_id].done
            )
        if busy:
            ring.pending_hourly = True
            return
        # 2C: refresh detected/expected from VisionWorker HTTP when available.
        snap = fetch_session_ratio(ring.session_id) or {}
        ratio_label = snap.get("ratio_label") or ring.last_ratio_label
        detected_total = snap.get("detected_total")
        expected_total = snap.get("expected_total")
        if ratio_label:
            ring.last_ratio_label = str(ratio_label)
        extra: dict[str, Any] = {
            "phase_code": ring.phase_code,
            "phase_name": ring.phase_name,
            "ratio_label": ratio_label,
            "catch_up": bool(catch_up or ring.pending_hourly),
        }
        if detected_total is not None:
            extra["detected_total"] = detected_total
        if expected_total is not None:
            extra["expected_total"] = expected_total
        if snap.get("source"):
            extra["ratio_source"] = snap["source"]
        self._handle_trigger({
            "session_id": ring.session_id,
            "reason": "hourly",
            "phase_id": ring.phase_id,
            "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "extra": extra,
        })
        with self._clip_lock:
            job = self._active_clips.get(ring.session_id)
            armed = bool(job and not job.done and job.reason == "hourly")
        # Clear pending once armed; mark_saved also clears after finalize.
        ring.pending_hourly = not armed

    def _hourly_tick(self) -> None:
        for ring in self.rings.all_sessions():
            self._maybe_hourly(ring, catch_up=False)

    def run_forever(self) -> None:
        logger.info(
            "EvidenceWorker started ring=%.1fs garage=%s idle_gap=%.0fs redis=%s",
            RING_SECONDS, GARAGE_ENABLED, IDLE_GAP_SECONDS, REDIS_URL.split("@")[-1],
        )
        client = self._client()
        while not self._stop.is_set():
            got_trigger = False
            try:
                self._drain_known_frames(client)
                item = client.blpop(TRIGGER_LIST_KEY, timeout=TRIGGER_BLOCK_S)
                if item:
                    got_trigger = True
                    _, raw = item
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        payload = None
                    if isinstance(payload, dict):
                        self._handle_trigger(payload)
                self._finalize_due_clips()
                self._hourly_tick()
                self._process_upload_retries()
            except Exception as exc:  # noqa: BLE001
                logger.exception("evidence loop error: %s", exc)
                time.sleep(0.5)
                continue
            if FRAME_POLL_MS > 0 and not got_trigger:
                time.sleep(FRAME_POLL_MS / 1000.0)
