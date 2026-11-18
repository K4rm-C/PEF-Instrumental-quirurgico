-- =============================================================================
-- 017_ms11_notificaciones_servicios.sql — MS-11 (R06 / R07)
--
-- 1. service_event: transitions of the technical availability of a component, written by the MonitoringService
--    collector (down after two consecutive failed cycles, recovered when it answers again). It is TECHNICAL
--    availability only: it never says anything about the clinical correctness of a count.
-- 2. Two notification types:
--      final_count_escalated  the Operator escalated a possible physical shortage / surplus after a rejected final
--                             count (audit session.final_count_escalated, MS-09). Until now the Supervisor only
--                             received the generic discrepancy notice.
--      service_alert          a component went down / recovered (IT administrators of every institution).
-- 3. The outbox accepts service_event rows (same trigger pattern as station_event, MS-06).
--
-- Idempotent and additive (applied at start-up by NotificationsService and MonitoringService under the shared
-- advisory lock pef:schema:migration, and by the db-migrate step of Docker Compose). Never deletes data.
-- =============================================================================

CREATE TABLE IF NOT EXISTS service_event (
  id           UUID         NOT NULL DEFAULT gen_random_uuid(),
  component    VARCHAR(40)  NOT NULL,
  kind         VARCHAR(16)  NOT NULL,
  status       VARCHAR(16)  NOT NULL,
  error_type   VARCHAR(64),
  occurred_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  detail       JSONB        NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT pk_service_event PRIMARY KEY (id),
  CONSTRAINT chk_service_event_kind CHECK (kind IN ('down', 'recovered')),
  CONSTRAINT chk_service_event_status CHECK (status IN ('up', 'degraded', 'down', 'unknown')),
  CONSTRAINT chk_service_event_detail CHECK (jsonb_typeof(detail) = 'object')
);
CREATE INDEX IF NOT EXISTS ix_service_event_component ON service_event (component, occurred_at DESC);

ALTER TABLE notification DROP CONSTRAINT IF EXISTS chk_notification_type;
-- MS-11: NOT VALID — re-applying this (idempotent) migration must not re-check rows written by NEWER types of
-- later migrations (e.g. 017 notification types); the constraint still applies to every new or updated row and
-- the last migration of the sequence defines the final allowed set.
ALTER TABLE notification ADD CONSTRAINT chk_notification_type CHECK (type IN (
  'session_scheduled', 'discrepancy_pending', 'correction_required', 'discrepancy_resolved', 'session_closed',
  'review_pending', 'session_unresolved', 'station_alert', 'final_count_escalated', 'service_alert')) NOT VALID;

ALTER TABLE notification_outbox DROP CONSTRAINT IF EXISTS chk_notification_outbox_table;
-- MS-11: NOT VALID — re-applying this (idempotent) migration must not re-check rows written by NEWER types of
-- later migrations (e.g. 017 notification types); the constraint still applies to every new or updated row and
-- the last migration of the sequence defines the final allowed set.
ALTER TABLE notification_outbox ADD CONSTRAINT chk_notification_outbox_table
  CHECK (source_table IN ('access_audit', 'count_event', 'station_event', 'service_event')) NOT VALID;

CREATE OR REPLACE FUNCTION notification_capture_service_event() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  BEGIN
    INSERT INTO notification_outbox (source_table, source_id, event_code, session_id, occurred_at)
    VALUES ('service_event', NEW.id, 'service.' || NEW.kind, NULL, NEW.occurred_at)
    ON CONFLICT (source_table, source_id) DO NOTHING;
  EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'notification outbox capture failed for service_event %: %', NEW.id, SQLERRM;
  END;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_notification_service_event ON service_event;
CREATE TRIGGER trg_notification_service_event
  AFTER INSERT ON service_event
  FOR EACH ROW EXECUTE FUNCTION notification_capture_service_event();

-- The escalation audit (MS-09, rf_session.escalate_final_mismatch) was never captured: the access_audit trigger of 006
-- only listened to session.scheduled and discrepancy.resolve. 017 redefines it (applied after 006, so it wins).
DROP TRIGGER IF EXISTS trg_notification_access_audit ON access_audit;
CREATE TRIGGER trg_notification_access_audit
  AFTER INSERT ON access_audit
  FOR EACH ROW
  WHEN (NEW.action IN ('session.scheduled', 'discrepancy.resolve', 'session.final_count_escalated')
        AND NEW.resource_type = 'work_session')
  EXECUTE FUNCTION notification_capture_access_audit();
