"""Monitoring collector process (`python collector.py`; compose service monitoring-collector).

Start-up: applies the idempotent migration (retrying while PostgreSQL is not ready), then loops:
one cycle every `MONITORING_INTERVAL_SECONDS` (every component checked once, in parallel, stored
in PostgreSQL) and the retention purge every hour. Each cycle is logged with every component's
status. If this process stops, nothing else is affected (no component depends on it); when it
starts again the cycles simply resume, and the API reports components as `unknown` (stale) for
the gap instead of repeating the last known state.
"""

from __future__ import annotations

import logging
import signal
import time

from config import Settings
from database import create_database, create_redis
from services import probes
from services.collector import Collector

logger = logging.getLogger("monitoring.collector")
_running = True
PURGE_EVERY_SECONDS = 3600


def _stop(*_):
    global _running
    _running = False


def _mongo(settings: Settings):
    if not settings.mongo_url:
        return None, None
    from pymongo import MongoClient
    timeout_ms = int(settings.check_timeout_seconds * 1000)
    client = MongoClient(settings.mongo_url, serverSelectionTimeoutMS=timeout_ms, connectTimeoutMS=timeout_ms,
                         socketTimeoutMS=timeout_ms, appname="pef-monitoring-collector")
    return client, probes.safe_endpoint(settings.mongo_url)


def main() -> None:
    settings = Settings.from_env(worker_only=True)
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    database = None
    while database is None and _running:
        try:
            database = create_database(settings)
        except Exception as exc:  # noqa: BLE001 - PostgreSQL not ready yet
            logger.warning("database not ready (%s); retrying", type(exc).__name__)
            time.sleep(3)
    if database is None:
        return
    mongo_client, mongo_endpoint = _mongo(settings)
    collector = Collector(settings, database, create_redis(settings), mongo_client, mongo_endpoint)
    configured = [name for name, url in settings.targets.items() if url] + (["mongo"] if mongo_client else [])
    logger.info("collector started; interval=%ss timeout=%ss retention=%sd; configured targets: %s",
                settings.interval_seconds, settings.check_timeout_seconds, settings.retention_days, ", ".join(configured))
    last_purge = 0.0
    while _running:
        cycle_started = time.monotonic()
        try:
            collector.run_once()
            if time.monotonic() - last_purge >= PURGE_EVERY_SECONDS:
                collector.purge()
                last_purge = time.monotonic()
        except Exception as exc:  # noqa: BLE001 - never die on one bad cycle
            logger.warning("cycle failed (%s); continuing", type(exc).__name__)
        pause = max(0.0, settings.interval_seconds - (time.monotonic() - cycle_started))
        while _running and pause > 0:
            time.sleep(min(pause, 1.0))
            pause -= 1.0
    collector.close()
    logger.info("collector stopped")


if __name__ == "__main__":
    main()
