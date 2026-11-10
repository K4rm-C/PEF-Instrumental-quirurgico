"""Per-session RAM ring of camera JPEGs (wall-clock window)."""

from __future__ import annotations

import base64
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque


@dataclass
class FrameItem:
    ts: float
    jpeg: bytes


@dataclass
class SessionRing:
    session_id: str
    max_seconds: float = 6.0
    _frames: Deque[FrameItem] = field(default_factory=deque)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    last_frame_at: float = 0.0
    last_saved_at: float = field(default_factory=time.time)
    phase_id: str | None = None
    phase_code: str | None = None
    phase_name: str | None = None
    last_ratio_label: str | None = None
    # Overdue hourly while idle → fire once when frames resume.
    pending_hourly: bool = False

    def push(self, jpeg: bytes, ts: float | None = None) -> None:
        if not jpeg:
            return
        now = float(ts if ts is not None else time.time())
        with self._lock:
            self._frames.append(FrameItem(ts=now, jpeg=jpeg))
            self.last_frame_at = now
            self._prune_unlocked(now)

    def _prune_unlocked(self, now: float) -> None:
        cutoff = now - float(self.max_seconds)
        while self._frames and self._frames[0].ts < cutoff:
            self._frames.popleft()

    def snapshot(self, *, before: float, until: float | None = None) -> list[FrameItem]:
        end = float(until if until is not None else time.time())
        start = end - float(before)
        with self._lock:
            self._prune_unlocked(end)
            return [FrameItem(ts=f.ts, jpeg=f.jpeg) for f in self._frames if start <= f.ts <= end]

    def mark_saved(self, when: float | None = None) -> None:
        self.last_saved_at = float(when if when is not None else time.time())


class RingRegistry:
    def __init__(self, *, max_seconds: float = 6.0) -> None:
        self.max_seconds = max_seconds
        self._sessions: dict[str, SessionRing] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> SessionRing:
        with self._lock:
            ring = self._sessions.get(session_id)
            if ring is None:
                ring = SessionRing(session_id=session_id, max_seconds=self.max_seconds)
                self._sessions[session_id] = ring
            return ring

    def all_sessions(self) -> list[SessionRing]:
        with self._lock:
            return list(self._sessions.values())


def decode_jpeg_b64(data: str) -> bytes | None:
    if not data:
        return None
    try:
        raw = base64.b64decode(data, validate=False)
    except Exception:
        return None
    return raw or None
