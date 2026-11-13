"""Integration worker: FHIR deliveries (with retries) and maintenance of the sync tables.

    python worker.py            # loop (compose: integration-worker)

Maintenance (once per hour):
- compaction of sync_change: a change older than SYNC_RETENTION_DAYS is deleted only when a LATER change of the
  same resource exists (the latest change of every resource — deletions and deactivations included — is kept
  forever), so a client without a cursor can always rebuild the full state;
- expired idempotency records (sync_operation.expires_at, 7 days) are deleted.
"""

from __future__ import annotations

import logging
import signal
import time

from sqlalchemy import text

from config import Settings
from database import create_database
from main import configure_logging
from services.deliveries import DeliveryService

logger = logging.getLogger("integration.worker")


def compact(database, retention_days: int) -> dict[str, int]:
    with database.begin() as connection:
        compacted = connection.execute(text("""
            DELETE FROM sync_change c
            WHERE c.changed_at < now() - make_interval(days => :days)
              AND EXISTS (SELECT 1 FROM sync_change later WHERE later.resource_type = c.resource_type
                          AND later.resource_id = c.resource_id AND later.seq > c.seq)
        """), {"days": retention_days}).rowcount
        expired = connection.execute(text("DELETE FROM sync_operation WHERE expires_at < now()")).rowcount
    return {"compacted": compacted, "expired_operations": expired}


def main() -> None:
    settings = Settings.from_env(worker_only=True)
    configure_logging(settings.log_level)
    database = create_database(settings)
    deliveries = DeliveryService(database, settings)
    running = {"on": True}
    signal.signal(signal.SIGTERM, lambda *_: running.update(on=False))
    logger.info("integration worker started (allowed hosts: %s)", ", ".join(settings.allowed_hosts) or "none")
    last_maintenance = 0.0
    while running["on"]:
        try:
            stats = deliveries.deliver_due()
            if any(stats.values()):
                logger.info("deliveries %s", stats)
            if time.monotonic() - last_maintenance > 3600:
                logger.info("maintenance %s", compact(database, settings.retention_days))
                last_maintenance = time.monotonic()
        except Exception:  # noqa: BLE001 - the loop must survive a database outage
            logger.exception("worker cycle failed")
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
