"""Assemble pre+post JPEG burst into local video (ffmpeg H.264 → WebM → OpenCV → JPEG)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from config import CLIPS_DIR, POST_SECONDS, PRE_SECONDS
from ring_buffer import FrameItem, SessionRing

logger = logging.getLogger(__name__)


@dataclass
class ClipJob:
    session_id: str
    reason: str
    phase_id: str | None
    occurred_at: str | None
    frames: list[FrameItem] = field(default_factory=list)
    post_until: float = 0.0
    done: bool = False
    phase_code: str | None = None
    phase_name: str | None = None
    ratio_label: str | None = None
    detected_total: int | None = None
    expected_total: int | None = None


def _decode_bgr(jpeg: bytes) -> np.ndarray | None:
    arr = np.frombuffer(jpeg, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return frame


def _fps_for(frames: list[FrameItem], decoded_n: int) -> float:
    duration = max(0.05, frames[-1].ts - frames[0].ts)
    if decoded_n <= 1:
        return 10.0
    return max(1.0, min(30.0, (decoded_n - 1) / duration))


def _decode_all(frames: list[FrameItem]) -> list[np.ndarray]:
    decoded: list[np.ndarray] = []
    for item in frames:
        img = _decode_bgr(item.jpeg)
        if img is not None:
            decoded.append(img)
    if not decoded:
        raise ValueError("no decodable JPEGs")
    h, w = decoded[0].shape[:2]
    for i, img in enumerate(decoded):
        if img.shape[0] != h or img.shape[1] != w:
            decoded[i] = cv2.resize(img, (w, h))
    return decoded


def _write_frame_dir(decoded: list[np.ndarray], tmp_dir: Path) -> None:
    for i, img in enumerate(decoded):
        path = tmp_dir / f"f_{i:05d}.jpg"
        ok = cv2.imwrite(str(path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise RuntimeError(f"failed to write temp frame {path}")


def _run_ffmpeg(args: list[str]) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", *args],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            logger.debug("ffmpeg failed: %s", (proc.stderr or "")[:400])
            return False
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("ffmpeg unavailable: %s", exc)
        return False


def _encode_ffmpeg_h264(decoded: list[np.ndarray], frames: list[FrameItem], out_path: Path) -> dict[str, Any] | None:
    fps = _fps_for(frames, len(decoded))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.mp4")
    with tempfile.TemporaryDirectory(prefix="pef_ev_") as td:
        tdir = Path(td)
        _write_frame_dir(decoded, tdir)
        ok = _run_ffmpeg([
            "-y",
            "-framerate", f"{fps:.3f}",
            "-i", str(tdir / "f_%05d.jpg"),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-an",
            str(tmp),
        ])
        if not ok or not tmp.is_file() or tmp.stat().st_size < 32:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            return None
    if out_path.exists():
        out_path.unlink()
    tmp.replace(out_path)
    duration = max(0.05, frames[-1].ts - frames[0].ts)
    h, w = decoded[0].shape[:2]
    return {
        "path": str(out_path),
        "frame_count": len(decoded),
        "fps": round(fps, 2),
        "width": w,
        "height": h,
        "size_bytes": out_path.stat().st_size,
        "duration_s": round(duration, 3),
        "kind": "mp4",
        "encoder": "ffmpeg_libx264",
    }


def _encode_ffmpeg_webm(decoded: list[np.ndarray], frames: list[FrameItem], out_path: Path) -> dict[str, Any] | None:
    fps = _fps_for(frames, len(decoded))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.webm")
    with tempfile.TemporaryDirectory(prefix="pef_ev_") as td:
        tdir = Path(td)
        _write_frame_dir(decoded, tdir)
        ok = False
        for codec in ("libvpx-vp9", "libvpx"):
            ok = _run_ffmpeg([
                "-y",
                "-framerate", f"{fps:.3f}",
                "-i", str(tdir / "f_%05d.jpg"),
                "-c:v", codec,
                "-pix_fmt", "yuv420p",
                "-an",
                "-b:v", "1M",
                str(tmp),
            ])
            if ok and tmp.is_file() and tmp.stat().st_size >= 32:
                break
            ok = False
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        if not ok:
            return None
    if out_path.exists():
        out_path.unlink()
    tmp.replace(out_path)
    duration = max(0.05, frames[-1].ts - frames[0].ts)
    h, w = decoded[0].shape[:2]
    return {
        "path": str(out_path),
        "frame_count": len(decoded),
        "fps": round(fps, 2),
        "width": w,
        "height": h,
        "size_bytes": out_path.stat().st_size,
        "duration_s": round(duration, 3),
        "kind": "webm",
        "encoder": "ffmpeg_webm",
    }


def _encode_opencv_mp4(decoded: list[np.ndarray], frames: list[FrameItem], out_path: Path) -> dict[str, Any] | None:
    fps = _fps_for(frames, len(decoded))
    h, w = decoded[0].shape[:2]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.mp4")
    writer = None
    fourcc_used = None
    for fourcc_name in ("avc1", "H264", "mp4v"):
        fourcc = cv2.VideoWriter_fourcc(*fourcc_name)
        writer = cv2.VideoWriter(str(tmp), fourcc, fps, (w, h))
        if writer.isOpened():
            fourcc_used = fourcc_name
            break
        writer.release()
        writer = None
    if writer is None:
        return None
    try:
        for img in decoded:
            writer.write(img)
    finally:
        writer.release()
    if not tmp.is_file() or tmp.stat().st_size < 32:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return None
    if out_path.exists():
        out_path.unlink()
    tmp.replace(out_path)
    duration = max(0.05, frames[-1].ts - frames[0].ts)
    return {
        "path": str(out_path),
        "frame_count": len(decoded),
        "fps": round(fps, 2),
        "width": w,
        "height": h,
        "size_bytes": out_path.stat().st_size,
        "duration_s": round(duration, 3),
        "kind": "mp4",
        "encoder": f"opencv_{fourcc_used}",
    }


def encode_clip(frames: list[FrameItem], out_dir: Path, *, reason: str, clip_id: str) -> dict[str, Any]:
    """Priority: ffmpeg H.264 MP4 → ffmpeg WebM → OpenCV MP4. Raises if all fail."""
    if not frames:
        raise ValueError("no frames to encode")
    decoded = _decode_all(frames)
    stem = f"{reason}_{clip_id}"
    mp4_path = out_dir / f"{stem}.mp4"
    webm_path = out_dir / f"{stem}.webm"

    meta = _encode_ffmpeg_h264(decoded, frames, mp4_path)
    if meta:
        return meta
    logger.warning("ffmpeg H.264 failed; trying WebM")
    meta = _encode_ffmpeg_webm(decoded, frames, webm_path)
    if meta:
        return meta
    logger.warning("ffmpeg WebM failed; trying OpenCV MP4")
    meta = _encode_opencv_mp4(decoded, frames, mp4_path)
    if meta:
        return meta
    raise RuntimeError("all video encoders failed")


# Backward-compatible alias used by smoke tests.
def encode_mp4(frames: list[FrameItem], out_path: Path) -> dict[str, Any]:
    decoded = _decode_all(frames)
    meta = _encode_ffmpeg_h264(decoded, frames, out_path)
    if meta:
        return meta
    meta = _encode_opencv_mp4(decoded, frames, out_path)
    if meta:
        return meta
    raise RuntimeError("OpenCV/ffmpeg VideoWriter failed to open")


def start_clip_job(
    ring: SessionRing,
    *,
    reason: str,
    phase_id: str | None,
    occurred_at: str | None,
    pre_seconds: float = PRE_SECONDS,
    post_seconds: float = POST_SECONDS,
    phase_code: str | None = None,
    phase_name: str | None = None,
    ratio_label: str | None = None,
    detected_total: int | None = None,
    expected_total: int | None = None,
) -> ClipJob:
    # Anchor on last camera ts (live wall clock, or tape time in tests).
    anchor = float(ring.last_frame_at or time.time())
    pre = ring.snapshot(before=pre_seconds, until=anchor)
    now = time.time()
    return ClipJob(
        session_id=ring.session_id,
        reason=reason,
        phase_id=phase_id or ring.phase_id,
        occurred_at=occurred_at,
        frames=list(pre),
        post_until=now + float(post_seconds),
        phase_code=phase_code or ring.phase_code,
        phase_name=phase_name or ring.phase_name,
        ratio_label=ratio_label if ratio_label is not None else ring.last_ratio_label,
        detected_total=detected_total,
        expected_total=expected_total,
    )


def append_live_frame(job: ClipJob, item: FrameItem) -> None:
    if job.done:
        return
    if item.ts <= job.post_until:
        job.frames.append(item)


def _write_sidecar(meta: dict[str, Any], media_path: Path) -> Path:
    """Persist clip metadata next to the media file for the portal catalog / retry."""
    import json

    side = Path(str(meta["sidecar_path"])) if meta.get("sidecar_path") else media_path.with_suffix(
        media_path.suffix + ".json"
    )
    payload = {
        "clip_id": meta.get("clip_id"),
        "session_id": meta.get("session_id"),
        "reason": meta.get("reason"),
        "phase_id": meta.get("phase_id"),
        "phase_code": meta.get("phase_code"),
        "phase_name": meta.get("phase_name"),
        "ratio_label": meta.get("ratio_label"),
        "detected_total": meta.get("detected_total"),
        "expected_total": meta.get("expected_total"),
        "occurred_at": meta.get("occurred_at"),
        "frame_count": meta.get("frame_count"),
        "size_bytes": meta.get("size_bytes"),
        "encoder": meta.get("encoder"),
        "kind": meta.get("kind") or (
            "jpeg" if media_path.suffix.lower() in {".jpg", ".jpeg"}
            else "webm" if media_path.suffix.lower() == ".webm"
            else "mp4"
        ),
        "local_path": str(media_path),
        "filename": media_path.name,
        "fallback": bool(meta.get("fallback")),
        "upload_status": meta.get("upload_status") or "local_only",
        "local_purged": bool(meta.get("local_purged")),
        "garage": meta.get("garage"),
    }
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    meta["sidecar_path"] = str(side)
    return side


def purge_local_media(meta: dict[str, Any]) -> None:
    """Delete local media after successful BD upload; keep sidecar for audit."""
    path = Path(str(meta.get("local_path") or meta.get("path") or ""))
    if path.is_file():
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("purge local media failed path=%s: %s", path, exc)
            return
    meta["local_purged"] = True
    meta["upload_status"] = "uploaded"
    if path:
        _write_sidecar(meta, path)


def finalize_clip(job: ClipJob) -> dict[str, Any]:
    job.done = True
    clip_id = uuid.uuid4().hex
    out_dir = CLIPS_DIR / job.session_id
    meta = encode_clip(job.frames, out_dir, reason=job.reason, clip_id=clip_id)
    media = Path(meta["path"])
    meta.update({
        "session_id": job.session_id,
        "reason": job.reason,
        "phase_id": job.phase_id,
        "phase_code": job.phase_code,
        "phase_name": job.phase_name,
        "ratio_label": job.ratio_label,
        "detected_total": job.detected_total,
        "expected_total": job.expected_total,
        "occurred_at": job.occurred_at,
        "clip_id": clip_id,
        "local_path": str(media),
    })
    _write_sidecar(meta, media)
    logger.info(
        "evidence clip ready session=%s reason=%s frames=%s encoder=%s path=%s ratio=%s",
        job.session_id, job.reason, meta["frame_count"], meta.get("encoder"), media, job.ratio_label,
    )
    return meta


def fallback_jpeg(job: ClipJob) -> dict[str, Any]:
    """If video encode fails, keep the last JPEG on disk."""
    job.done = True
    if not job.frames:
        raise ValueError("no frames for jpeg fallback")
    clip_id = uuid.uuid4().hex
    out = CLIPS_DIR / job.session_id / f"{job.reason}_{clip_id}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(job.frames[-1].jpeg)
    meta = {
        "session_id": job.session_id,
        "reason": job.reason,
        "phase_id": job.phase_id,
        "phase_code": job.phase_code,
        "phase_name": job.phase_name,
        "ratio_label": job.ratio_label,
        "detected_total": job.detected_total,
        "expected_total": job.expected_total,
        "occurred_at": job.occurred_at,
        "clip_id": clip_id,
        "local_path": str(out),
        "path": str(out),
        "frame_count": 1,
        "size_bytes": out.stat().st_size,
        "kind": "jpeg",
        "encoder": "jpeg_fallback",
        "fallback": True,
    }
    _write_sidecar(meta, out)
    return meta
