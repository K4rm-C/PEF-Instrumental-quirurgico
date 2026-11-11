"""Durable consumer of notification_outbox (run by worker.py; one or more instances).

Guarantees:
- No lost events: the producers' triggers write the outbox row in the producer's own transaction;
  a periodic reconciliation sweep re-reads the source tables (access_audit / count_event of the last
  `reconcile_window_days`, never before the stream's installation) and enqueues anything missing.
- No duplicates: uk_notification_outbox_source (one outbox row per source row) and
  uk_notification_source_recipient (one notification per event, recipient and type), both with
  ON CONFLICT DO NOTHING; a row is marked processed in the same transaction as its notifications.
- Concurrency: rows are claimed with FOR UPDATE SKIP LOCKED, so several workers never take the
  same event.
- Failures: a failing event is retried (attempts / last_error); after `max_attempts` it stays
  unprocessed and is reported in the logs instead of blocking the others.
NotificationsService being down only delays the notifications: the outbox keeps accumulating.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from database import Database
from services import dispatcher, email_channel

logger = logging.getLogger("notifications.worker")

RECONCILE_SQL = """
INSERT INTO notification_outbox (source_table, source_id, event_code, session_id, occurred_at)
SELECT 'access_audit', a.id, a.action, a.resource_id, a.occurred_at
FROM access_audit a
WHERE a.action IN ('session.scheduled', 'discrepancy.resolve', 'session.final_count_escalated')
  AND a.resource_type = 'work_session'
  AND a.occurred_at >= :since
UNION ALL
SELECT 'count_event', e.id, t.code, e.session_id, e.occurred_at
FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id
WHERE t.code IN ('manual_close', 'session_close') AND e.occurred_at >= :since
ON CONFLICT (source_table, source_id) DO NOTHING
"""

# MS-06: station alerts (station_event from 012; the sweep is skipped on a database without it).
RECONCILE_STATION_SQL = """
INSERT INTO notification_outbox (source_table, source_id, event_code, session_id, occurred_at)
SELECT 'station_event', e.id, 'station.' || e.kind, NULL, e.occurred_at
FROM station_event e
WHERE e.kind IN ('heartbeat_lost', 'camera_error') AND e.occurred_at >= :since
ON CONFLICT (source_table, source_id) DO NOTHING
"""

# MS-11: technical availability transitions (service_event from 017; skipped on a database without it).
RECONCILE_SERVICE_SQL = """
INSERT INTO notification_outbox (source_table, source_id, event_code, session_id, occurred_at)
SELECT 'service_event', e.id, 'service.' || e.kind, NULL, e.occurred_at
FROM service_event e
WHERE e.occurred_at >= :since
ON CONFLICT (source_table, source_id) DO NOTHING
"""


class OutboxWorker:
    def __init__(self, database: Database, *, batch_size: int = 50, max_attempts: int = 10,
                 reconcile_window_days: int = 7, email: email_channel.EmailSettings | None = None,
                 sender=None):
        self.database = database
        # MS-06 e-mail channel: disabled unless configured (NOTIFICATIONS_SMTP_HOST + _FROM).
        self.email = email or email_channel.EmailSettings()
        self.sender = sender or email_channel.send
        self.email_types = self.email.types if self.email.enabled else ()
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.reconcile_window_days = reconcile_window_days

    def reconcile(self) -> int:
        """Enqueue relevant source rows the triggers did not capture (safety net)."""
        with self.database.begin() as connection:
            installed = connection.execute(text(
                "SELECT installed_at FROM notification_stream WHERE name = 'business_events'")).scalar()
            since = max(installed, datetime.now(timezone.utc) - timedelta(days=self.reconcile_window_days)) \
                if installed else datetime.now(timezone.utc) - timedelta(days=self.reconcile_window_days)
            added = connection.execute(text(RECONCILE_SQL), {"since": since}).rowcount
            if connection.execute(text("SELECT to_regclass('station_event') IS NOT NULL")).scalar():
                added += connection.execute(text(RECONCILE_STATION_SQL), {"since": since}).rowcount
            if connection.execute(text("SELECT to_regclass('service_event') IS NOT NULL")).scalar():
                added += connection.execute(text(RECONCILE_SERVICE_SQL), {"since": since}).rowcount
        if added:
            logger.warning("reconciliation enqueued %d event(s) the triggers had not captured", added)
        return added

    def process_batch(self, skip_ids=()) -> dict[str, int]:
        """Process up to batch_size claimable events (except `skip_ids`); failing ids go to stats["failed_ids"]."""
        stats = {"events": 0, "notifications": 0, "failed": 0, "failed_ids": []}
        with self.database.begin() as connection:
            events = connection.execute(text("""
                SELECT id, source_table, source_id, event_code, session_id, attempts
                FROM notification_outbox
                WHERE processed_at IS NULL AND attempts < :max_attempts AND NOT (id = ANY(:skip))
                ORDER BY id LIMIT :limit
                FOR UPDATE SKIP LOCKED
            """), {"max_attempts": self.max_attempts, "limit": self.batch_size,
                   "skip": list(skip_ids)}).mappings().all()
            for event in events:
                event = dict(event)
                savepoint = connection.begin_nested()
                try:
                    created = dispatcher.store(connection, event, dispatcher.plan(connection, event), self.email_types)
                    connection.execute(text("UPDATE notification_outbox SET processed_at = now(), last_error = NULL "
                                            "WHERE id = :id"), {"id": event["id"]})
                    savepoint.commit()
                    stats["events"] += 1
                    stats["notifications"] += created
                    logger.info("event %s %s/%s session=%s -> %d notification(s)", event["id"], event["source_table"],
                                event["event_code"], event["session_id"], created)
                except Exception as exc:  # noqa: BLE001 - one bad event must not stop the batch
                    savepoint.rollback()
                    stats["failed"] += 1
                    stats["failed_ids"].append(event["id"])
                    connection.execute(text("UPDATE notification_outbox SET attempts = attempts + 1, last_error = :err "
                                            "WHERE id = :id"), {"id": event["id"], "err": f"{type(exc).__name__}: {exc}"[:255]})
                    if event["attempts"] + 1 >= self.max_attempts:
                        logger.error("event %s gave up after %d attempts: %s", event["id"], event["attempts"] + 1, exc)
                    else:
                        logger.warning("event %s failed (attempt %d): %s", event["id"], event["attempts"] + 1, exc)
        return stats

    def drain(self) -> dict[str, int]:
        """Process until the outbox has nothing claimable (tests and the start-up catch-up).

        Each failing event is attempted once per drain: it is retried by later polls, not in a tight loop.
        """
        total = {"events": 0, "notifications": 0, "failed": 0}
        failed: list[int] = []
        while True:
            stats = self.process_batch(failed)
            failed.extend(stats.pop("failed_ids"))
            for key in total:
                total[key] += stats[key]
            if stats["events"] + stats["failed"] == 0:
                return total

    def deliver_emails(self, limit: int = 50) -> dict[str, int]:
        """Send pending e-mail deliveries (one transaction each). A failure is recorded and retried later;
        the in-app notification is never affected."""
        stats = {"sent": 0, "failed": 0}
        if not self.email.enabled:
            return stats
        for _ in range(limit):
            with self.database.begin() as connection:
                row = connection.execute(text("""
                    SELECT d.id, d.attempts, n.type, n.data, n.created_at, u.email
                    FROM notification_delivery d
                    JOIN notification n ON n.id = d.notification_id
                    JOIN "user" u ON u.id = n.recipient_user_id
                    WHERE d.status = 'pending' AND d.channel = 'email' AND d.attempts < :max
                    ORDER BY d.id LIMIT 1 FOR UPDATE OF d SKIP LOCKED
                """), {"max": self.max_attempts}).mappings().first()
                if row is None:
                    return stats
                try:
                    message = email_channel.build_message(self.email, dict(row), row["email"])
                    self.sender(self.email, message)
                except Exception as exc:  # noqa: BLE001 - mail server down / refused: retried, never fatal
                    attempts = row["attempts"] + 1
                    connection.execute(text("""
                        UPDATE notification_delivery SET attempts = :a, last_error = :e,
                               status = CASE WHEN :a >= :max THEN 'failed' ELSE 'pending' END
                        WHERE id = :id
                    """), {"a": attempts, "e": f"{type(exc).__name__}: {exc}"[:255], "max": self.max_attempts,
                           "id": row["id"]})
                    stats["failed"] += 1
                    logger.warning("e-mail delivery %s failed (attempt %d): %s", row["id"], attempts, type(exc).__name__)
                    return stats   # the server is probably down: try again in the next cycle
                connection.execute(text("""
                    UPDATE notification_delivery SET status = 'sent', sent_at = now(), attempts = attempts + 1,
                           last_error = NULL WHERE id = :id
                """), {"id": row["id"]})
                stats["sent"] += 1
        return stats

    def pending(self) -> int:
        with self.database.connect() as connection:
            return connection.execute(text("SELECT count(*) FROM notification_outbox WHERE processed_at IS NULL")).scalar_one()
