-- PEF 010 (ADMIN-01, RF-AD-10 / RF-AD-08): Administration settings and vision-model class names.
--
-- app_setting: application parameters editable by the it_admin of each institution (KPI thresholds,
--   password policy, upload size / MIME limits). One row per (institution, key); a missing row means
--   "use the documented default" (services/admin_local/settings.py), so no seed rows are needed.
-- yolo_model.class_names: class id -> name read from the uploaded .pt (or names.yaml / JSON), kept
--   so the mapping step can be repeated. Nullable; existing models and VisionWorker are unaffected.
--
-- Idempotent and data-preserving: IF NOT EXISTS only; nothing is dropped or rewritten.
BEGIN;

CREATE TABLE IF NOT EXISTS app_setting (
  institution_id     UUID         NOT NULL,
  key                VARCHAR(64)  NOT NULL,
  value              JSONB        NOT NULL,
  updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_by_user_id UUID,
  CONSTRAINT pk_app_setting PRIMARY KEY (institution_id, key),
  CONSTRAINT fk_app_setting_institution
    FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE CASCADE,
  CONSTRAINT fk_app_setting_user
    FOREIGN KEY (updated_by_user_id) REFERENCES "user" (id) ON DELETE SET NULL
);

ALTER TABLE yolo_model ADD COLUMN IF NOT EXISTS class_names JSONB;

COMMIT;
