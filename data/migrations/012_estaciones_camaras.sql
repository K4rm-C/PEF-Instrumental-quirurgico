-- =============================================================================
-- 012 · MS-05 Cameras, station heartbeat and technical alerts
-- Incremental, idempotent and data-preserving: CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS only;
-- nothing existing is dropped or rewritten. Safe to run on every start-up (MonitoringService applies it
-- after 007) and on a new database (docker-compose initdb).
--
-- capture_camera  : cameras registered by Administration for a capture station (RF-AD-07 complement).
--                   institution_id is denormalised for tenant filters and is checked against the
--                   station's room by a trigger (a camera can never belong to another institution).
-- station_status  : current reported / observed state of a station (one row per station).
--                   last_seen_at   = last authenticated heartbeat (station client / Operator capture page);
--                   camera_*       = what the client reported about its camera;
--                   frames_seen_at = last time VisionWorker itself received frames of a session at this
--                                    station (server-side evidence that the camera really works);
--                   frames_camera_id = the camera of that session.
-- station_event   : technical alerts and their recovery (heartbeat_lost / heartbeat_recovered,
--                   camera_error / camera_recovered). An alert is open while resolved_at IS NULL.
-- work_session.camera_id : camera used for the capture (evidence; counting rules unchanged).
-- =============================================================================

CREATE TABLE IF NOT EXISTS capture_camera (
  id                 UUID         NOT NULL DEFAULT gen_random_uuid(),
  institution_id     UUID         NOT NULL,
  station_id         UUID         NOT NULL,
  name               VARCHAR(120) NOT NULL,
  device_type        VARCHAR(20)  NOT NULL DEFAULT 'usb',
  resolution_width   SMALLINT,
  resolution_height  SMALLINT,
  fps                SMALLINT,
  roi                JSONB,
  active             BOOLEAN      NOT NULL DEFAULT TRUE,
  created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_capture_camera PRIMARY KEY (id),
  CONSTRAINT fk_capture_camera_institution FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE CASCADE,
  CONSTRAINT fk_capture_camera_station FOREIGN KEY (station_id) REFERENCES capture_station (id) ON DELETE CASCADE,
  CONSTRAINT uk_capture_camera_station_name UNIQUE (station_id, name),
  CONSTRAINT chk_capture_camera_device_type CHECK (device_type IN ('usb', 'integrated', 'ip', 'other')),
  CONSTRAINT chk_capture_camera_resolution CHECK (
    (resolution_width IS NULL AND resolution_height IS NULL)
    OR (resolution_width BETWEEN 160 AND 7680 AND resolution_height BETWEEN 120 AND 4320)),
  CONSTRAINT chk_capture_camera_fps CHECK (fps IS NULL OR fps BETWEEN 1 AND 120),
  CONSTRAINT chk_capture_camera_roi_object CHECK (roi IS NULL OR jsonb_typeof(roi) = 'object')
);
CREATE INDEX IF NOT EXISTS ix_capture_camera_station ON capture_camera (station_id);
CREATE INDEX IF NOT EXISTS ix_capture_camera_institution ON capture_camera (institution_id);

CREATE OR REPLACE FUNCTION capture_camera_same_institution() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  station_institution UUID;
BEGIN
  SELECT r.institution_id INTO station_institution
  FROM capture_station s JOIN operating_room r ON r.id = s.room_id WHERE s.id = NEW.station_id;
  IF station_institution IS DISTINCT FROM NEW.institution_id THEN
    RAISE EXCEPTION 'capture_camera % : institution % does not own station %', NEW.id, NEW.institution_id, NEW.station_id
      USING ERRCODE = 'check_violation';
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_capture_camera_same_institution ON capture_camera;
CREATE TRIGGER trg_capture_camera_same_institution
  BEFORE INSERT OR UPDATE ON capture_camera
  FOR EACH ROW EXECUTE FUNCTION capture_camera_same_institution();

ALTER TABLE work_session ADD COLUMN IF NOT EXISTS camera_id UUID;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_work_session_camera') THEN
    ALTER TABLE work_session ADD CONSTRAINT fk_work_session_camera
      FOREIGN KEY (camera_id) REFERENCES capture_camera (id) ON DELETE SET NULL;
  END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS station_status (
  station_id          UUID         NOT NULL,
  institution_id      UUID         NOT NULL,
  last_seen_at        TIMESTAMPTZ,
  last_reported_by    UUID,
  client              VARCHAR(40),
  camera_id           UUID,
  camera_reported     VARCHAR(16)  NOT NULL DEFAULT 'unknown',
  camera_error        VARCHAR(120),
  camera_reported_at  TIMESTAMPTZ,
  frames_seen_at      TIMESTAMPTZ,
  frames_session_id   UUID,
  updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_station_status PRIMARY KEY (station_id),
  CONSTRAINT fk_station_status_station FOREIGN KEY (station_id) REFERENCES capture_station (id) ON DELETE CASCADE,
  CONSTRAINT fk_station_status_institution FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE CASCADE,
  CONSTRAINT fk_station_status_camera FOREIGN KEY (camera_id) REFERENCES capture_camera (id) ON DELETE SET NULL,
  CONSTRAINT chk_station_status_camera CHECK (camera_reported IN ('ok', 'error', 'unavailable', 'unknown'))
);
CREATE INDEX IF NOT EXISTS ix_station_status_institution ON station_status (institution_id);
-- Camera of the session whose frames VisionWorker received (frames_seen_at belongs to this camera).
ALTER TABLE station_status ADD COLUMN IF NOT EXISTS frames_camera_id UUID;

CREATE TABLE IF NOT EXISTS station_event (
  id              UUID         NOT NULL DEFAULT gen_random_uuid(),
  institution_id  UUID         NOT NULL,
  station_id      UUID         NOT NULL,
  camera_id       UUID,
  kind            VARCHAR(32)  NOT NULL,
  detail          JSONB        NOT NULL DEFAULT '{}'::jsonb,
  occurred_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  resolved_at     TIMESTAMPTZ,
  CONSTRAINT pk_station_event PRIMARY KEY (id),
  CONSTRAINT fk_station_event_station FOREIGN KEY (station_id) REFERENCES capture_station (id) ON DELETE CASCADE,
  CONSTRAINT fk_station_event_institution FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE CASCADE,
  CONSTRAINT fk_station_event_camera FOREIGN KEY (camera_id) REFERENCES capture_camera (id) ON DELETE SET NULL,
  CONSTRAINT chk_station_event_kind CHECK (kind IN ('heartbeat_lost', 'heartbeat_recovered', 'camera_error', 'camera_recovered')),
  CONSTRAINT chk_station_event_detail CHECK (jsonb_typeof(detail) = 'object'),
  -- Recovery events are facts, never "open" alerts.
  CONSTRAINT chk_station_event_resolved CHECK (kind IN ('heartbeat_lost', 'camera_error') OR resolved_at IS NULL)
);
CREATE INDEX IF NOT EXISTS ix_station_event_station_time ON station_event (station_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_station_event_open ON station_event (institution_id, occurred_at DESC) WHERE resolved_at IS NULL;
-- At most one open alert of each kind per station (concurrent heartbeats cannot duplicate an alert).
CREATE UNIQUE INDEX IF NOT EXISTS uk_station_event_open_kind ON station_event (station_id, kind)
  WHERE resolved_at IS NULL AND kind IN ('heartbeat_lost', 'camera_error');

-- MS-06: station alerts feed the notification outbox. The capture function comes from 013 (NotificationsService);
-- whichever of 012 / 013 runs second creates the trigger.
DO $$
BEGIN
  IF to_regprocedure('notification_capture_station_event()') IS NOT NULL THEN
    DROP TRIGGER IF EXISTS trg_notification_station_event ON station_event;
    CREATE TRIGGER trg_notification_station_event
      AFTER INSERT ON station_event
      FOR EACH ROW
      WHEN (NEW.kind IN ('heartbeat_lost', 'camera_error'))
      EXECUTE FUNCTION notification_capture_station_event();
  END IF;
END;
$$;
