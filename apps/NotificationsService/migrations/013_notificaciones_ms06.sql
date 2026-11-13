-- =============================================================================
-- 013 · MS-06 Notifications: new event types, station alerts and the e-mail channel
-- Incremental, idempotent and data-preserving (existing notifications and outbox rows are kept as they are).
-- Applied by NotificationsService at start-up after 006, and by initdb.
--
-- New notification types (from events that already exist; nothing new is produced to notify):
--   review_pending      count_event manual_close with payload.next_status = awaiting_spd_review (supervisor)
--   session_unresolved  count_event session_close with payload.safe_close_confirmed = false (operator + supervisor)
--   station_alert       station_event heartbeat_lost / camera_error (MS-05; supervisors and administrators)
-- notification_delivery: one row per notification and extra channel (e-mail). The in-app notification is
--   committed first and never depends on it: a failing mail server only leaves the delivery `failed`.
-- =============================================================================

ALTER TABLE notification DROP CONSTRAINT IF EXISTS chk_notification_type;
-- MS-11: NOT VALID — re-applying this (idempotent) migration must not re-check rows written by NEWER types of
-- later migrations (e.g. 017 notification types); the constraint still applies to every new or updated row and
-- the last migration of the sequence defines the final allowed set.
ALTER TABLE notification ADD CONSTRAINT chk_notification_type CHECK (type IN (
  'session_scheduled', 'discrepancy_pending', 'correction_required', 'discrepancy_resolved', 'session_closed',
  'review_pending', 'session_unresolved', 'station_alert')) NOT VALID;

ALTER TABLE notification_outbox DROP CONSTRAINT IF EXISTS chk_notification_outbox_table;
-- MS-11: NOT VALID — re-applying this (idempotent) migration must not re-check rows written by NEWER types of
-- later migrations (e.g. 017 notification types); the constraint still applies to every new or updated row and
-- the last migration of the sequence defines the final allowed set.
ALTER TABLE notification_outbox ADD CONSTRAINT chk_notification_outbox_table
  CHECK (source_table IN ('access_audit', 'count_event', 'station_event')) NOT VALID;

CREATE TABLE IF NOT EXISTS notification_delivery (
  id               BIGSERIAL    NOT NULL,
  notification_id  UUID         NOT NULL,
  channel          VARCHAR(16)  NOT NULL,
  status           VARCHAR(16)  NOT NULL DEFAULT 'pending',
  attempts         SMALLINT     NOT NULL DEFAULT 0,
  last_error       VARCHAR(255),
  created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
  sent_at          TIMESTAMPTZ,
  CONSTRAINT pk_notification_delivery PRIMARY KEY (id),
  CONSTRAINT fk_notification_delivery_notification FOREIGN KEY (notification_id) REFERENCES notification (id) ON DELETE CASCADE,
  CONSTRAINT uk_notification_delivery_channel UNIQUE (notification_id, channel),
  CONSTRAINT chk_notification_delivery_channel CHECK (channel IN ('email')),
  CONSTRAINT chk_notification_delivery_status CHECK (status IN ('pending', 'sent', 'failed'))
);
CREATE INDEX IF NOT EXISTS ix_notification_delivery_pending ON notification_delivery (id) WHERE status = 'pending';

CREATE OR REPLACE FUNCTION notification_capture_station_event() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  BEGIN
    INSERT INTO notification_outbox (source_table, source_id, event_code, session_id, occurred_at)
    VALUES ('station_event', NEW.id, 'station.' || NEW.kind, NULL, NEW.occurred_at)
    ON CONFLICT (source_table, source_id) DO NOTHING;
  EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'notification outbox capture failed for station_event %: %', NEW.id, SQLERRM;
  END;
  RETURN NEW;
END;
$$;

-- station_event comes from 012 (MonitoringService). If it does not exist yet, 012 creates this trigger
-- itself when it runs; the worker's reconciliation sweep covers any gap.
DO $$
BEGIN
  IF to_regclass('station_event') IS NOT NULL THEN
    DROP TRIGGER IF EXISTS trg_notification_station_event ON station_event;
    CREATE TRIGGER trg_notification_station_event
      AFTER INSERT ON station_event
      FOR EACH ROW
      WHEN (NEW.kind IN ('heartbeat_lost', 'camera_error'))
      EXECUTE FUNCTION notification_capture_station_event();
  END IF;
END;
$$;
