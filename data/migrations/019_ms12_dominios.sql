-- =============================================================================
-- 019_ms12_dominios.sql — MS-12: technically defined gaps of the 20 domains (no new process, no counting rule).
--
-- 1. kit_item_equivalence (ms-kits-charolas): an instrument family AUTHORIZED as equivalent to a family of a kit
--    (owner: SPD Supervisor, CatalogService). It is catalog information shown to the Operator and the Supervisor; it
--    does NOT change the expected quantities, the reference count or the final-count rule. Whether a validated
--    equivalent must also close a classification discrepancy is a functional decision (docs/DECISIONES_PENDIENTES.md).
-- 2. capture_camera_calibration (ms-camaras): calibrations MEASURED by a technician (reference object of known length
--    in the image). PEF only derives mm/pixel from the two measured values; it never invents a physical parameter.
--    Immutable history (the latest row is the current calibration).
-- 3. tracking_event (ms-tracking-instrumental): the complete event log of the tracker for the ACCEPTED final run,
--    persisted by the portal (traceability; never a count).
-- 4. yolo_model_regression_run (ms-modelos-ia): reproducible regression runs of a model against a baseline model on a
--    fixed set of reference videos (counts measured by real inference). Agreement with the baseline only; NOT accuracy.
-- 5. integration_export_batch (+ items) (ms-integracion-hospitalaria): traceable batch exports of CLOSED sessions
--    (own documented JSON / XML format, SHA-256 of the exact document, who / when / which sessions). No clinical data.
--
-- Idempotent and additive; applied by the db-migrate step of Docker Compose (and initdb for a new database).
-- =============================================================================

CREATE TABLE IF NOT EXISTS kit_item_equivalence (
  id                     UUID          NOT NULL DEFAULT gen_random_uuid(),
  institution_id         UUID          NOT NULL,
  kit_id                 UUID          NOT NULL,
  family_id              UUID          NOT NULL,
  equivalent_family_id   UUID          NOT NULL,
  notes                  VARCHAR(300),
  active                 BOOLEAN       NOT NULL DEFAULT TRUE,
  authorized_by_user_id  UUID          NOT NULL,
  authorized_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
  revoked_by_user_id     UUID,
  revoked_at             TIMESTAMPTZ,
  CONSTRAINT pk_kit_item_equivalence PRIMARY KEY (id),
  CONSTRAINT fk_kit_item_equivalence_kit FOREIGN KEY (kit_id) REFERENCES kit (id) ON DELETE CASCADE,
  CONSTRAINT fk_kit_item_equivalence_family FOREIGN KEY (family_id) REFERENCES instrument_family (id) ON DELETE RESTRICT,
  CONSTRAINT fk_kit_item_equivalence_equivalent FOREIGN KEY (equivalent_family_id) REFERENCES instrument_family (id) ON DELETE RESTRICT,
  CONSTRAINT fk_kit_item_equivalence_institution FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE CASCADE,
  CONSTRAINT chk_kit_item_equivalence_distinct CHECK (family_id <> equivalent_family_id),
  CONSTRAINT chk_kit_item_equivalence_revoked CHECK ((revoked_at IS NULL) = active)
);
-- One ACTIVE authorization per (kit, family, equivalent); revoked rows stay as history.
CREATE UNIQUE INDEX IF NOT EXISTS uk_kit_item_equivalence_active
  ON kit_item_equivalence (kit_id, family_id, equivalent_family_id) WHERE active;
CREATE INDEX IF NOT EXISTS ix_kit_item_equivalence_kit ON kit_item_equivalence (kit_id);

CREATE TABLE IF NOT EXISTS capture_camera_calibration (
  id                   UUID           NOT NULL DEFAULT gen_random_uuid(),
  institution_id       UUID           NOT NULL,
  camera_id            UUID           NOT NULL,
  method               VARCHAR(30)    NOT NULL,
  reference_length_mm  NUMERIC(9, 3)  NOT NULL,
  measured_pixels      NUMERIC(9, 3)  NOT NULL,
  mm_per_pixel         NUMERIC(12, 6) NOT NULL,
  frame_width          SMALLINT       NOT NULL,
  frame_height         SMALLINT       NOT NULL,
  notes                VARCHAR(300),
  performed_by_user_id UUID           NOT NULL,
  performed_at         TIMESTAMPTZ    NOT NULL DEFAULT now(),
  CONSTRAINT pk_capture_camera_calibration PRIMARY KEY (id),
  CONSTRAINT fk_capture_camera_calibration_camera FOREIGN KEY (camera_id) REFERENCES capture_camera (id) ON DELETE CASCADE,
  CONSTRAINT fk_capture_camera_calibration_institution FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE CASCADE,
  CONSTRAINT chk_capture_camera_calibration_method CHECK (method IN ('reference_length')),
  CONSTRAINT chk_capture_camera_calibration_values CHECK (reference_length_mm > 0 AND measured_pixels > 0 AND mm_per_pixel > 0),
  CONSTRAINT chk_capture_camera_calibration_frame CHECK (frame_width BETWEEN 160 AND 7680 AND frame_height BETWEEN 120 AND 4320)
);
CREATE INDEX IF NOT EXISTS ix_capture_camera_calibration_camera ON capture_camera_calibration (camera_id, performed_at DESC);

CREATE TABLE IF NOT EXISTS tracking_event (
  id             BIGSERIAL      NOT NULL,
  session_id     UUID           NOT NULL,
  count_event_id UUID           NOT NULL,
  run_id         VARCHAR(32),
  seq            INTEGER        NOT NULL,
  video_t        NUMERIC(10, 3) NOT NULL,
  frame_index    INTEGER        NOT NULL,
  track_id       INTEGER        NOT NULL,
  event          VARCHAR(20)    NOT NULL,
  family_code    VARCHAR(64),
  confidence     NUMERIC(5, 3),
  detail         JSONB          NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT pk_tracking_event PRIMARY KEY (id),
  CONSTRAINT fk_tracking_event_session FOREIGN KEY (session_id) REFERENCES work_session (id) ON DELETE CASCADE,
  CONSTRAINT fk_tracking_event_count FOREIGN KEY (count_event_id) REFERENCES count_event (id) ON DELETE CASCADE,
  CONSTRAINT uk_tracking_event_seq UNIQUE (count_event_id, seq),
  CONSTRAINT chk_tracking_event_kind CHECK (event IN ('confirmed', 'lost', 'recovered', 'terminated', 'class_change',
                                                      'enter_roi', 'exit_roi')),
  CONSTRAINT chk_tracking_event_detail CHECK (jsonb_typeof(detail) = 'object')
);
CREATE INDEX IF NOT EXISTS ix_tracking_event_session ON tracking_event (session_id, seq);

CREATE TABLE IF NOT EXISTS yolo_model_regression_run (
  id                   UUID          NOT NULL DEFAULT gen_random_uuid(),
  model_id             UUID          NOT NULL,
  baseline_model_id    UUID          NOT NULL,
  reference_set        VARCHAR(120)  NOT NULL,
  reference_set_sha256 CHAR(64)      NOT NULL,
  items                JSONB         NOT NULL,
  items_total          INTEGER       NOT NULL,
  items_identical      INTEGER       NOT NULL,
  runner               VARCHAR(80)   NOT NULL,
  notes                VARCHAR(300),
  recorded_by_user_id  UUID          NOT NULL,
  executed_at          TIMESTAMPTZ   NOT NULL,
  created_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
  CONSTRAINT pk_yolo_model_regression_run PRIMARY KEY (id),
  CONSTRAINT fk_yolo_model_regression_model FOREIGN KEY (model_id) REFERENCES yolo_model (id) ON DELETE CASCADE,
  CONSTRAINT fk_yolo_model_regression_baseline FOREIGN KEY (baseline_model_id) REFERENCES yolo_model (id) ON DELETE CASCADE,
  CONSTRAINT chk_yolo_model_regression_items CHECK (jsonb_typeof(items) = 'array' AND items_total > 0
                                                    AND items_identical BETWEEN 0 AND items_total),
  CONSTRAINT chk_yolo_model_regression_sha CHECK (reference_set_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX IF NOT EXISTS ix_yolo_model_regression_model ON yolo_model_regression_run (model_id, executed_at DESC);

CREATE TABLE IF NOT EXISTS integration_export_batch (
  id                 UUID          NOT NULL DEFAULT gen_random_uuid(),
  institution_id     UUID          NOT NULL,
  format             VARCHAR(8)    NOT NULL,
  period_from        TIMESTAMPTZ   NOT NULL,
  period_to          TIMESTAMPTZ   NOT NULL,
  sessions_count     INTEGER       NOT NULL,
  content            BYTEA         NOT NULL,
  content_sha256     CHAR(64)      NOT NULL,
  created_by_user_id UUID,
  created_by_client_id UUID,
  created_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
  CONSTRAINT pk_integration_export_batch PRIMARY KEY (id),
  CONSTRAINT fk_integration_export_batch_institution FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE CASCADE,
  CONSTRAINT chk_integration_export_batch_format CHECK (format IN ('json', 'xml')),
  CONSTRAINT chk_integration_export_batch_period CHECK (period_from < period_to),
  CONSTRAINT chk_integration_export_batch_actor CHECK ((created_by_user_id IS NULL) <> (created_by_client_id IS NULL)),
  CONSTRAINT chk_integration_export_batch_sha CHECK (content_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX IF NOT EXISTS ix_integration_export_batch_institution ON integration_export_batch (institution_id, created_at DESC);

CREATE TABLE IF NOT EXISTS integration_export_item (
  batch_id    UUID NOT NULL,
  session_id  UUID NOT NULL,
  CONSTRAINT pk_integration_export_item PRIMARY KEY (batch_id, session_id),
  CONSTRAINT fk_integration_export_item_batch FOREIGN KEY (batch_id) REFERENCES integration_export_batch (id) ON DELETE CASCADE,
  CONSTRAINT fk_integration_export_item_session FOREIGN KEY (session_id) REFERENCES work_session (id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_integration_export_item_session ON integration_export_item (session_id);

-- Immutable histories: a calibration, a regression run, an export or a tracking event is never edited afterwards.
CREATE OR REPLACE FUNCTION ms12_immutable_row() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME USING ERRCODE = 'check_violation';
END;
$$;
DROP TRIGGER IF EXISTS trg_capture_camera_calibration_immutable ON capture_camera_calibration;
CREATE TRIGGER trg_capture_camera_calibration_immutable BEFORE UPDATE ON capture_camera_calibration
  FOR EACH ROW EXECUTE FUNCTION ms12_immutable_row();
DROP TRIGGER IF EXISTS trg_yolo_model_regression_immutable ON yolo_model_regression_run;
CREATE TRIGGER trg_yolo_model_regression_immutable BEFORE UPDATE ON yolo_model_regression_run
  FOR EACH ROW EXECUTE FUNCTION ms12_immutable_row();
DROP TRIGGER IF EXISTS trg_integration_export_batch_immutable ON integration_export_batch;
CREATE TRIGGER trg_integration_export_batch_immutable BEFORE UPDATE ON integration_export_batch
  FOR EACH ROW EXECUTE FUNCTION ms12_immutable_row();
DROP TRIGGER IF EXISTS trg_tracking_event_immutable ON tracking_event;
CREATE TRIGGER trg_tracking_event_immutable BEFORE UPDATE ON tracking_event
  FOR EACH ROW EXECUTE FUNCTION ms12_immutable_row();
