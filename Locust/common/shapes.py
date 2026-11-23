"""Locust LoadTestShape stages for PEF performance profiles (max ~10 minutes)."""
from __future__ import annotations

from locust import LoadTestShape


class StagesShape(LoadTestShape):
    """Generic staged shape. Subclasses set `stages` = list of dicts.

    Each stage: {"duration": seconds_from_start, "users": int, "spawn_rate": float}
    """

    stages: list[dict] = []

    def tick(self):  # noqa: ANN201 — Locust API
        run_time = self.get_run_time()
        for stage in self.stages:
            if run_time < stage["duration"]:
                return (stage["users"], stage["spawn_rate"])
        return None


class BaselineShape(StagesShape):
    """Calm traffic — latency baseline (~3 min)."""

    stages = [
        {"duration": 180, "users": 5, "spawn_rate": 1},
    ]


class LoadShape(StagesShape):
    """Expected concurrent load sustained (~5 min)."""

    stages = [
        {"duration": 60, "users": 10, "spawn_rate": 2},
        {"duration": 300, "users": 20, "spawn_rate": 2},
    ]


class SpikeShape(StagesShape):
    """Sudden peak then recovery (~4 min)."""

    stages = [
        {"duration": 45, "users": 5, "spawn_rate": 2},
        {"duration": 90, "users": 40, "spawn_rate": 20},
        {"duration": 150, "users": 40, "spawn_rate": 5},
        {"duration": 240, "users": 5, "spawn_rate": 5},
    ]


class StressShape(StagesShape):
    """Ramp until high concurrency (~8 min)."""

    stages = [
        {"duration": 60, "users": 10, "spawn_rate": 2},
        {"duration": 180, "users": 25, "spawn_rate": 2},
        {"duration": 300, "users": 40, "spawn_rate": 2},
        {"duration": 480, "users": 50, "spawn_rate": 2},
    ]


class SoakShape(StagesShape):
    """Endurance at moderate load (capped at 10 min)."""

    stages = [
        {"duration": 60, "users": 10, "spawn_rate": 2},
        {"duration": 600, "users": 15, "spawn_rate": 1},
    ]
