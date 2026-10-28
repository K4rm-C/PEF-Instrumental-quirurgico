-- =============================================================================
-- 006 · Notifications Service (PEF-DS09 §1.3.6 complement): internal notifications
-- Idempotent: safe to run on a new or an existing database (no data is dropped or changed).
--
-- Sources (existing, persisted business events — nothing new is invented):
--   access_audit.action  'session.scheduled'   -> session_scheduled      (assigned operator)
--   access_audit.action  'discrepancy.resolve' -> discrepancy_resolved   (assigned operator)
--   count_event 'manual_close' with payload.next_status = 'correction_required'
--                                              -> discrepancy_pending    (supervisor)
--                                              -> correction_required    (assigned operator)
--   count_event 'session_close'                -> session_closed         (operator + supervisor)
--
-- Capture: AFTER INSERT triggers copy the reference of each relevant row into
-- notification_outbox in the SAME transaction as the producer (atomic, durable, producers
-- unchanged). A trigger never aborts the producer: on any error it raises a WARNING and the
-- worker's reconciliation sweep picks the row up from the source table later.
-- =============================================================================

CREATE TABLE IF NOT EXISTS notification_stream (
  name         VARCHAR(32)  NOT NULL,
  installed_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_notification_stream PRIMARY KEY (name)
);
-- Notifications start at installation: older events are history, not news.
INSERT INTO notification_stream (name) VALUES ('business_events') ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS notification_outbox (
  id            BIGSERIAL    NOT NULL,
  source_table  VARCHAR(32)  NOT NULL,
  source_id     UUID         NOT NULL,
  event_code    VARCHAR(64)  NOT NULL,
  session_id    UUID,
  occurred_at   TIMESTAMPTZ  NOT NULL,
  enqueued_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
  processed_at  TIMESTAMPTZ,
  attempts      SMALLINT     NOT NULL DEFAULT 0,
  last_error    VARCHAR(255),
  CONSTRAINT pk_notification_outbox PRIMARY KEY (id),
  CONSTRAINT uk_notification_outbox_source UNIQUE (source_table, source_id),
  CONSTRAINT chk_notification_outbox_table CHECK (source_table IN ('access_audit', 'count_event'))
);
CREATE INDEX IF NOT EXISTS ix_notification_outbox_pending ON notification_outbox (id) WHERE processed_at IS NULL;

CREATE TABLE IF NOT EXISTS notification (
  id                 UUID         NOT NULL DEFAULT gen_random_uuid(),
  recipient_user_id  UUID         NOT NULL,
  institution_id     UUID         NOT NULL,
  type               VARCHAR(32)  NOT NULL,
  status             VARCHAR(16)  NOT NULL DEFAULT 'unread',
  session_id         UUID,
  data               JSONB        NOT NULL DEFAULT '{}'::jsonb,
  source_table       VARCHAR(32)  NOT NULL,
  source_id          UUID         NOT NULL,
  created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  read_at            TIMESTAMPTZ,
  CONSTRAINT pk_notification PRIMARY KEY (id),
  CONSTRAINT fk_notification_recipient FOREIGN KEY (recipient_user_id) REFERENCES "user" (id) ON DELETE CASCADE,
  CONSTRAINT fk_notification_institution FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE CASCADE,
  -- One notification per event, recipient and type: re-processing an event never duplicates it.
  CONSTRAINT uk_notification_source_recipient UNIQUE (source_table, source_id, recipient_user_id, type),
  CONSTRAINT chk_notification_type CHECK (type IN ('session_scheduled', 'discrepancy_pending', 'correction_required',
                                                   'discrepancy_resolved', 'session_closed')),
  CONSTRAINT chk_notification_status CHECK (status IN ('unread', 'read')),
  CONSTRAINT chk_notification_read_at CHECK ((status = 'read') = (read_at IS NOT NULL)),
  CONSTRAINT chk_notification_data_object CHECK (jsonb_typeof(data) = 'object')
);
CREATE INDEX IF NOT EXISTS ix_notification_recipient ON notification (recipient_user_id, institution_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_notification_unread ON notification (recipient_user_id) WHERE status = 'unread';

CREATE OR REPLACE FUNCTION notification_capture_access_audit() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  BEGIN
    INSERT INTO notification_outbox (source_table, source_id, event_code, session_id, occurred_at)
    VALUES ('access_audit', NEW.id, NEW.action, NEW.resource_id, NEW.occurred_at)
    ON CONFLICT (source_table, source_id) DO NOTHING;
  EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'notification outbox capture failed for access_audit %: %', NEW.id, SQLERRM;
  END;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION notification_capture_count_event() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  event_code VARCHAR(64);
BEGIN
  BEGIN
    SELECT code INTO event_code FROM cat_event_type WHERE id = NEW.event_type_id;
    IF event_code IN ('manual_close', 'session_close') THEN
      INSERT INTO notification_outbox (source_table, source_id, event_code, session_id, occurred_at)
      VALUES ('count_event', NEW.id, event_code, NEW.session_id, NEW.occurred_at)
      ON CONFLICT (source_table, source_id) DO NOTHING;
    END IF;
  EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'notification outbox capture failed for count_event %: %', NEW.id, SQLERRM;
  END;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_notification_access_audit ON access_audit;
CREATE TRIGGER trg_notification_access_audit
  AFTER INSERT ON access_audit
  FOR EACH ROW
  WHEN (NEW.action IN ('session.scheduled', 'discrepancy.resolve') AND NEW.resource_type = 'work_session')
  EXECUTE FUNCTION notification_capture_access_audit();

DROP TRIGGER IF EXISTS trg_notification_count_event ON count_event;
CREATE TRIGGER trg_notification_count_event
  AFTER INSERT ON count_event
  FOR EACH ROW
  EXECUTE FUNCTION notification_capture_count_event();
