"""One collector cycle: check every component once, in parallel, and store the results.

- Each probe has its own timeout and runs in its own thread: a component that hangs or refuses
  connections is recorded as down without delaying the others (the cycle lasts about one timeout).
- The cycle is stored in one transaction (monitoring_run + one monitoring_check per component).
  If PostgreSQL itself is down the cycle cannot be stored: it is logged and the next one retries.
- Load: one request per component per cycle (default every 30 s) to its cheapest readiness
  endpoint; the PostgreSQL checks are SELECT 1 and two small pg_stat_activity / outbox queries.
- Retention: rows older than `retention_days` are deleted (in batches) by `purge()`.
"""

from __future__ import annotations

import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import timedelta
from typing import Callable

from sqlalchemy import text

import requests

from config import COMPONENTS, Settings
from database import Database
from services import probes
from services.stations import StationService

logger = logging.getLogger("monitoring.collector")
PURGE_BATCH = 5000


class Collector:
    def __init__(self, settings: Settings, database: Database, redis_client, mongo_client=None, mongo_endpoint=None,
                 probe_engine=None):
        self.settings = settings
        self.database = database
        # Engine of the PostgreSQL probes (they run in parallel threads, each with its own pooled connection).
        self.probe_engine = probe_engine if probe_engine is not None else database.engine
        self.redis = redis_client
        self.mongo = mongo_client
        self.mongo_endpoint = mongo_endpoint
        self.pool = ThreadPoolExecutor(max_workers=len(COMPONENTS), thread_name_prefix="probe")
        self.stations = StationService(database, settings.station_stale_seconds, settings.camera_frames_seconds)

    def _probe(self, name: str, kind: str) -> Callable[[], probes.Result]:
        s, timeout, slow = self.settings, self.settings.check_timeout_seconds, self.settings.degraded_latency_ms
        engine = self.probe_engine
        if kind in ("http_health", "vision", "garage"):
            base = s.targets.get(name, "")
            if not base:
                return lambda: probes.not_configured(name)
            function = {"http_health": probes.http_health, "vision": probes.vision, "garage": probes.garage}[kind]
            return lambda: function(name, base, timeout, slow)
        if kind == "postgres":
            return lambda: probes.postgres(name, engine, timeout, slow)
        if kind == "redis":
            return lambda: probes.redis_ping(name, self.redis, timeout, slow)
        if kind == "mongo":
            if self.mongo is None:
                return lambda: probes.not_configured(name)
            return lambda: probes.mongo(name, self.mongo, self.mongo_endpoint, timeout, slow)
        if kind == "notifications_worker":
            return lambda: probes.notifications_worker(name, engine, timeout, slow, s.worker_backlog_seconds)
        if kind == "integration_worker":   # MS-12
            return lambda: probes.integration_worker(name, engine, timeout, slow, s.worker_backlog_seconds)
        return lambda: probes.not_configured(name)

    def check_all(self) -> list[probes.Result]:
        futures = {name: self.pool.submit(self._probe(name, kind)) for name, (_, kind) in COMPONENTS.items()}
        results = []
        for name, future in futures.items():
            try:
                results.append(future.result(timeout=self.settings.check_timeout_seconds + 2))
            except FutureTimeout:
                results.append(probes.Result(name, "down", probes.now(), error_type="timeout"))
            except Exception as exc:  # noqa: BLE001 - a broken probe must not stop the cycle
                logger.exception("probe %s failed", name)
                results.append(probes.Result(name, "unknown", probes.now(), error_type="probe_error",
                                             details={"exception": type(exc).__name__}))
        return results

    def store(self, results: list[probes.Result]) -> uuid.UUID:
        run_id = uuid.uuid4()
        started = min(r.checked_at for r in results)
        finished = probes.now()
        with self.database.begin() as connection:
            connection.execute(text("""
                INSERT INTO monitoring_run (id, started_at, finished_at, components, failures)
                VALUES (:id, :started, :finished, :components, :failures)
            """), {"id": run_id, "started": started, "finished": finished, "components": len(results),
                   "failures": sum(1 for r in results if r.status in ("down", "degraded"))})
            connection.execute(text("""
                INSERT INTO monitoring_check (id, run_id, component, checked_at, status, latency_ms, http_status,
                                              dependencies, error_type, endpoint, details)
                VALUES (:id, :run, :component, :checked_at, :status, :latency, :http, CAST(:deps AS jsonb), :error,
                        :endpoint, CAST(:details AS jsonb))
            """), [{"id": uuid.uuid4(), "run": run_id, "component": r.component, "checked_at": r.checked_at,
                    "status": r.status, "latency": r.latency_ms, "http": r.http_status,
                    "deps": json.dumps(r.dependencies), "error": r.error_type, "endpoint": r.endpoint,
                    "details": json.dumps(r.details)} for r in results])
        return run_id

    def run_once(self) -> tuple[uuid.UUID | None, list[probes.Result]]:
        results = self.check_all()
        summary = " ".join(f"{r.component}={r.status}" + (f"({r.error_type})" if r.error_type else "") for r in results)
        try:
            run_id = self.store(results)
        except Exception as exc:  # noqa: BLE001 - PostgreSQL down: the cycle is logged, the next retries
            logger.warning("cycle not stored (%s): %s", type(exc).__name__, summary)
            return None, results
        logger.info("run %s: %s", run_id, summary)
        self.service_transitions(run_id, results)
        self.stations_cycle()
        return run_id, results

    def service_transitions(self, run_id: uuid.UUID, results: list[probes.Result]) -> list[tuple[str, str]]:
        """MS-11 (R06/R07): records availability TRANSITIONS in service_event (017), which notifies the IT
        administrators. Debounced: `down` only after two consecutive failed cycles, `recovered` when a component
        that was reported down answers `up` again. `unknown` (not configured) and `degraded` never alert. Technical
        availability only. Never breaks the cycle (best effort; skipped while 017 is not applied)."""
        written: list[tuple[str, str]] = []
        try:
            with self.database.begin() as connection:
                if not connection.execute(text("SELECT to_regclass('service_event') IS NOT NULL")).scalar():
                    return written
                for result in results:
                    if result.status not in ("down", "up"):
                        continue
                    last = connection.execute(text(
                        "SELECT kind FROM service_event WHERE component = :c ORDER BY occurred_at DESC, id DESC LIMIT 1"),
                        {"c": result.component}).scalar()
                    kind = None
                    if result.status == "down" and last != "down":
                        previous = connection.execute(text("""
                            SELECT status FROM monitoring_check WHERE component = :c AND run_id <> :run
                            ORDER BY checked_at DESC LIMIT 1"""), {"c": result.component, "run": run_id}).scalar()
                        if previous == "down":
                            kind = "down"
                    elif result.status == "up" and last == "down":
                        kind = "recovered"
                    if kind:
                        connection.execute(text("""
                            INSERT INTO service_event (component, kind, status, error_type, occurred_at, detail)
                            VALUES (:c, :k, :s, :e, :t, CAST(:d AS jsonb))"""),
                            {"c": result.component, "k": kind, "s": result.status, "e": result.error_type,
                             "t": result.checked_at, "d": json.dumps({"run_id": str(run_id),
                                                                     "latency_ms": result.latency_ms})})
                        written.append((result.component, kind))
        except Exception as exc:  # noqa: BLE001 - alerting must never stop the health checks
            logger.warning("service transitions not recorded (%s)", type(exc).__name__)
            return []
        for component, kind in written:
            logger.warning("component %s: %s", component, kind)
        return written

    def vision_activity(self) -> list[dict]:
        """Sessions whose live camera frames VisionWorker received (GET {vision}/activity); [] if unavailable."""
        base = self.settings.targets.get("vision", "")
        if not base:
            return []
        try:
            response = requests.get(base + "/activity", timeout=self.settings.check_timeout_seconds)
            response.raise_for_status()
            sessions = response.json().get("sessions")
            return sessions if isinstance(sessions, list) else []
        except (requests.RequestException, ValueError) as exc:
            logger.info("vision activity not available (%s)", type(exc).__name__)
            return []

    def stations_cycle(self) -> dict[str, int]:
        """MS-05: camera evidence observed by VisionWorker + heartbeat_lost alerts. Never breaks the cycle."""
        stats = {"frames": 0, "lost": 0}
        try:
            stats["frames"] = self.stations.observe_frames(self.vision_activity())
            stats["lost"] = self.stations.detect_lost()
            if stats["lost"]:
                logger.warning("stations without heartbeat for %ss: %d new alert(s)", self.settings.station_stale_seconds,
                               stats["lost"])
        except Exception as exc:  # noqa: BLE001 - stations housekeeping must not stop the health checks
            logger.warning("stations cycle failed (%s)", type(exc).__name__)
        return stats

    def purge(self) -> int:
        """Delete checks and runs older than the retention window (bounded batches, never a long lock)."""
        cutoff = probes.now() - timedelta(days=self.settings.retention_days)
        deleted = 0
        while True:
            with self.database.begin() as connection:
                count = connection.execute(text("""
                    DELETE FROM monitoring_run WHERE id IN (
                        SELECT id FROM monitoring_run WHERE started_at < :cutoff ORDER BY started_at LIMIT :batch)
                """), {"cutoff": cutoff, "batch": PURGE_BATCH}).rowcount
            deleted += count
            if count < PURGE_BATCH:
                break
        if deleted:
            logger.info("retention: deleted %d run(s) older than %d day(s) (their checks cascade)", deleted,
                        self.settings.retention_days)
        return deleted

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
