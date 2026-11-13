-- =============================================================================
-- 018_ms11_camaras_modelos.sql — MS-11 (R08 / R17)
--
-- 1. capture_camera.device_type 'simulated': a test source (recorded video, virtual device) registered as such.
--    Its configuration travels to VisionWorker marked simulated=true; a capture made with it is NEVER evidence that
--    physical hardware works (MS-11 rule: a simulation does not validate a camera).
-- 2. yolo_model_evaluation: evaluation results of a registered model on a named, versioned dataset, as RECORDED
--    by an IT administrator from an offline evaluation (e.g. Ultralytics `val`). PEF never computes, estimates or
--    fills these numbers: an absent metric stays NULL. Rows are immutable (a correction is a new dataset version
--    or split), so the history of what was declared, by whom and when is kept.
--
-- Idempotent and additive; applied by the db-migrate step of Docker Compose (and initdb for a new database).
-- =============================================================================

ALTER TABLE capture_camera DROP CONSTRAINT IF EXISTS chk_capture_camera_device_type;
-- MS-11: NOT VALID — re-applying this (idempotent) migration must not re-check rows written by NEWER types of
-- later migrations (e.g. 017 notification types); the constraint still applies to every new or updated row and
-- the last migration of the sequence defines the final allowed set.
ALTER TABLE capture_camera ADD CONSTRAINT chk_capture_camera_device_type
  CHECK (device_type IN ('usb', 'integrated', 'ip', 'other', 'simulated')) NOT VALID;

CREATE TABLE IF NOT EXISTS yolo_model_evaluation (
  id                   UUID          NOT NULL DEFAULT gen_random_uuid(),
  model_id             UUID          NOT NULL,
  dataset_name         VARCHAR(120)  NOT NULL,
  dataset_version      VARCHAR(40)   NOT NULL,
  dataset_sha256       CHAR(64),
  split                VARCHAR(12)   NOT NULL,
  images               INTEGER       NOT NULL,
  instances            INTEGER,
  precision_value      NUMERIC(6, 4),
  recall_value         NUMERIC(6, 4),
  map50                NUMERIC(6, 4),
  map50_95             NUMERIC(6, 4),
  per_class            JSONB,
  conf_threshold       NUMERIC(4, 3),
  iou_threshold        NUMERIC(4, 3),
  source               VARCHAR(20)   NOT NULL,
  evaluated_at         TIMESTAMPTZ   NOT NULL,
  notes                VARCHAR(500),
  recorded_by_user_id  UUID          NOT NULL,
  created_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
  CONSTRAINT pk_yolo_model_evaluation PRIMARY KEY (id),
  CONSTRAINT fk_yolo_model_evaluation_model FOREIGN KEY (model_id) REFERENCES yolo_model (id) ON DELETE CASCADE,
  CONSTRAINT fk_yolo_model_evaluation_user FOREIGN KEY (recorded_by_user_id) REFERENCES "user" (id) ON DELETE RESTRICT,
  CONSTRAINT uk_yolo_model_evaluation UNIQUE (model_id, dataset_name, dataset_version, split),
  CONSTRAINT chk_yolo_model_evaluation_split CHECK (split IN ('validation', 'test', 'field')),
  CONSTRAINT chk_yolo_model_evaluation_source CHECK (source IN ('ultralytics_val', 'external', 'manual')),
  CONSTRAINT chk_yolo_model_evaluation_counts CHECK (images > 0 AND (instances IS NULL OR instances >= 0)),
  CONSTRAINT chk_yolo_model_evaluation_metrics CHECK (
    (precision_value IS NULL OR precision_value BETWEEN 0 AND 1) AND (recall_value IS NULL OR recall_value BETWEEN 0 AND 1)
    AND (map50 IS NULL OR map50 BETWEEN 0 AND 1) AND (map50_95 IS NULL OR map50_95 BETWEEN 0 AND 1)),
  CONSTRAINT chk_yolo_model_evaluation_thresholds CHECK (
    (conf_threshold IS NULL OR conf_threshold BETWEEN 0 AND 1) AND (iou_threshold IS NULL OR iou_threshold BETWEEN 0 AND 1)),
  CONSTRAINT chk_yolo_model_evaluation_sha CHECK (dataset_sha256 IS NULL OR dataset_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT chk_yolo_model_evaluation_per_class CHECK (per_class IS NULL OR jsonb_typeof(per_class) = 'object')
);
CREATE INDEX IF NOT EXISTS ix_yolo_model_evaluation_model ON yolo_model_evaluation (model_id, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS ix_yolo_model_evaluation_dataset ON yolo_model_evaluation (dataset_name, dataset_version, split);

-- Immutable rows: a declared evaluation is never edited (corrections are a new dataset version or split).
CREATE OR REPLACE FUNCTION yolo_model_evaluation_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'yolo_model_evaluation rows are immutable (record a new dataset version instead)'
    USING ERRCODE = 'check_violation';
END;
$$;
DROP TRIGGER IF EXISTS trg_yolo_model_evaluation_immutable ON yolo_model_evaluation;
CREATE TRIGGER trg_yolo_model_evaluation_immutable
  BEFORE UPDATE ON yolo_model_evaluation
  FOR EACH ROW EXECUTE FUNCTION yolo_model_evaluation_immutable();
