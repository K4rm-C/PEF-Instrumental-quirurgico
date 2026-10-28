"""Notifications outbox consumer process (`python worker.py`; compose service notifications-worker).

Start-up: applies the idempotent migration, reconciles the source tables and drains every pending
event (the catch-up after an outage), then loops: process batches, sleep `worker_poll_seconds`
when idle, reconcile every `reconcile_every_seconds`. Each processed event is logged.
"""

from __future__ import annotations

import logging
import signal
import time

from config import Settings
from database import create_database
from services.outbox_worker import OutboxWorker

logger = logging.getLogger("notifications.worker")
_running = True


def _stop(*_):
    global _running
    _running = False


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
    worker = OutboxWorker(database, batch_size=settings.worker_batch_size, max_attempts=settings.worker_max_attempts,
                          reconcile_window_days=settings.reconcile_window_days)
    logger.info("worker started; pending events=%d", worker.pending())
    last_reconcile = 0.0
    while _running:
        try:
            if time.monotonic() - last_reconcile >= settings.reconcile_every_seconds:
                worker.reconcile()
                last_reconcile = time.monotonic()
            stats = worker.drain()   # each failing event is tried once per cycle, then again after the pause
            if stats["events"] or stats["failed"]:
                logger.info("cycle: %(events)d event(s), %(notifications)d notification(s), %(failed)d failed", stats)
        except Exception as exc:  # noqa: BLE001 - PostgreSQL outage: keep retrying
            logger.warning("worker cycle failed (%s); retrying", type(exc).__name__)
        time.sleep(settings.worker_poll_seconds)
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
