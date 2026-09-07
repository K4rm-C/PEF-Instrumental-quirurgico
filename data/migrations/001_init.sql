-- =============================================================================
-- PEF · Conteo y trazabilidad de instrumental quirurgico
-- 001_init.sql · Esquema base PostgreSQL
--
-- Requiere PostgreSQL 15 o superior (gen_random_uuid nativo via pgcrypto).
-- Fuente: docs/data/esquema_base_datos_v2.md, seccion 5.
-- Este archivo es GENERADO a partir de ese documento. No editar a mano:
-- editar el documento y volver a extraer.
--
-- Orden de ejecucion:
--   001_init.sql            estructura (este archivo)
--   002_seed_catalogs.sql   catalogos + aviso de privacidad  (TODOS los ambientes)
--   003_seed_demo.sql       datos de demostracion            (SOLO desarrollo)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------------------
-- 5.2 Catálogos (cat_*)
-- -----------------------------------------------------------------------------

CREATE TABLE cat_gender (
  id   UUID        NOT NULL DEFAULT gen_random_uuid(),
  code VARCHAR(32) NOT NULL,
  name VARCHAR(80) NOT NULL,
  CONSTRAINT pk_cat_gender PRIMARY KEY (id),
  CONSTRAINT uk_cat_gender_code UNIQUE (code)
);

CREATE TABLE cat_specialty (
  id   UUID         NOT NULL DEFAULT gen_random_uuid(),
  code VARCHAR(64)  NOT NULL,
  name VARCHAR(160) NOT NULL,
  CONSTRAINT pk_cat_specialty PRIMARY KEY (id),
  CONSTRAINT uk_cat_specialty_code UNIQUE (code)
);

CREATE TABLE cat_procedure_type (
  id   UUID         NOT NULL DEFAULT gen_random_uuid(),
  code VARCHAR(64)  NOT NULL,
  name VARCHAR(200) NOT NULL,
  CONSTRAINT pk_cat_procedure_type PRIMARY KEY (id),
  CONSTRAINT uk_cat_procedure_type_code UNIQUE (code)
);

CREATE TABLE cat_surgical_role (
  id   UUID         NOT NULL DEFAULT gen_random_uuid(),
  code VARCHAR(64)  NOT NULL,
  name VARCHAR(120) NOT NULL,
  CONSTRAINT pk_cat_surgical_role PRIMARY KEY (id),
  CONSTRAINT uk_cat_surgical_role_code UNIQUE (code)
);

CREATE TABLE cat_operation_status (
  id   UUID        NOT NULL DEFAULT gen_random_uuid(),
  code VARCHAR(32) NOT NULL,
  name VARCHAR(80) NOT NULL,
  CONSTRAINT pk_cat_operation_status PRIMARY KEY (id),
  CONSTRAINT uk_cat_operation_status_code UNIQUE (code)
);

CREATE TABLE cat_session_status (
  id   UUID        NOT NULL DEFAULT gen_random_uuid(),
  code VARCHAR(32) NOT NULL,
  name VARCHAR(80) NOT NULL,
  CONSTRAINT pk_cat_session_status PRIMARY KEY (id),
  CONSTRAINT uk_cat_session_status_code UNIQUE (code)
);

CREATE TABLE cat_instrument_cycle_status (
  id   UUID        NOT NULL DEFAULT gen_random_uuid(),
  code VARCHAR(32) NOT NULL,
  name VARCHAR(80) NOT NULL,
  CONSTRAINT pk_cat_instrument_cycle_status PRIMARY KEY (id),
  CONSTRAINT uk_cat_instrument_cycle_status_code UNIQUE (code)
);

CREATE TABLE cat_instrument_category (
  id   UUID         NOT NULL DEFAULT gen_random_uuid(),
  code VARCHAR(64)  NOT NULL,
  name VARCHAR(120) NOT NULL,
  CONSTRAINT pk_cat_instrument_category PRIMARY KEY (id),
  CONSTRAINT uk_cat_instrument_category_code UNIQUE (code)
);

CREATE TABLE cat_usage_context (
  id   UUID         NOT NULL DEFAULT gen_random_uuid(),
  code VARCHAR(64)  NOT NULL,
  name VARCHAR(200) NOT NULL,
  CONSTRAINT pk_cat_usage_context PRIMARY KEY (id),
  CONSTRAINT uk_cat_usage_context_code UNIQUE (code)
);

CREATE TABLE cat_discrepancy_reason (
  id   UUID         NOT NULL DEFAULT gen_random_uuid(),
  code VARCHAR(64)  NOT NULL,
  name VARCHAR(160) NOT NULL,
  CONSTRAINT pk_cat_discrepancy_reason PRIMARY KEY (id),
  CONSTRAINT uk_cat_discrepancy_reason_code UNIQUE (code)
);

CREATE TABLE cat_checkpoint_reason (
  id   UUID        NOT NULL DEFAULT gen_random_uuid(),
  code VARCHAR(32) NOT NULL,
  name VARCHAR(80) NOT NULL,
  CONSTRAINT pk_cat_checkpoint_reason PRIMARY KEY (id),
  CONSTRAINT uk_cat_checkpoint_reason_code UNIQUE (code)
);

CREATE TABLE cat_operation_phase (
  id     UUID         NOT NULL DEFAULT gen_random_uuid(),
  code   VARCHAR(32)  NOT NULL,
  name   VARCHAR(80)  NOT NULL,
  active BOOLEAN      NOT NULL DEFAULT TRUE,
  CONSTRAINT pk_cat_operation_phase PRIMARY KEY (id),
  CONSTRAINT uk_cat_operation_phase_code UNIQUE (code)
);

CREATE TABLE cat_event_type (
  id   UUID         NOT NULL DEFAULT gen_random_uuid(),
  code VARCHAR(64)  NOT NULL,
  name VARCHAR(160) NOT NULL,
  CONSTRAINT pk_cat_event_type PRIMARY KEY (id),
  CONSTRAINT uk_cat_event_type_code UNIQUE (code)
);

CREATE TABLE cat_processing_purpose (
  id     UUID         NOT NULL DEFAULT gen_random_uuid(),
  code   VARCHAR(64)  NOT NULL,
  name   VARCHAR(160) NOT NULL,
  active BOOLEAN      NOT NULL DEFAULT TRUE,
  CONSTRAINT pk_cat_processing_purpose PRIMARY KEY (id),
  CONSTRAINT uk_cat_processing_purpose_code UNIQUE (code),
  CONSTRAINT chk_cat_processing_purpose_code CHECK (
    code IN ('quality_ops', 'model_improvement', 'external_sharing')
  )
);

-- -----------------------------------------------------------------------------
-- 5.3 Institución e identidad
-- -----------------------------------------------------------------------------

CREATE TABLE institution (
  id        UUID         NOT NULL DEFAULT gen_random_uuid(),
  name      VARCHAR(200) NOT NULL,
  active    BOOLEAN      NOT NULL DEFAULT TRUE,
  parent_id UUID,
  CONSTRAINT pk_institution PRIMARY KEY (id),
  CONSTRAINT fk_institution_parent
    FOREIGN KEY (parent_id) REFERENCES institution (id) ON DELETE SET NULL
);

CREATE TABLE role (
  id             UUID         NOT NULL DEFAULT gen_random_uuid(),
  code           VARCHAR(64)  NOT NULL,
  description    VARCHAR(255) NOT NULL,
  institution_id UUID         NOT NULL,
  CONSTRAINT pk_role PRIMARY KEY (id),
  CONSTRAINT fk_role_institution
    FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE CASCADE,
  CONSTRAINT uk_role_institution_code UNIQUE (institution_id, code)
);

CREATE TABLE "user" (
  id                   UUID         NOT NULL DEFAULT gen_random_uuid(),
  name                 VARCHAR(160) NOT NULL,
  active               BOOLEAN      NOT NULL DEFAULT TRUE,
  email                VARCHAR(255) NOT NULL,
  password_hash        TEXT         NOT NULL,
  password_updated_at  TIMESTAMPTZ,
  last_login_at        TIMESTAMPTZ,
  ui_preferences       JSONB        NOT NULL DEFAULT '{"locale":"en","theme":"light"}'::jsonb,
  created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
  institution_id       UUID         NOT NULL,
  CONSTRAINT pk_user PRIMARY KEY (id),
  CONSTRAINT uk_user_email UNIQUE (email),
  CONSTRAINT fk_user_institution
    FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE RESTRICT,
  CONSTRAINT chk_user_ui_preferences_object CHECK (jsonb_typeof(ui_preferences) = 'object')
);

CREATE TABLE user_role (
  id          UUID        NOT NULL DEFAULT gen_random_uuid(),
  assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  user_id     UUID        NOT NULL,
  role_id     UUID        NOT NULL,
  CONSTRAINT pk_user_role PRIMARY KEY (id),
  CONSTRAINT fk_user_role_user
    FOREIGN KEY (user_id) REFERENCES "user" (id) ON DELETE CASCADE,
  CONSTRAINT fk_user_role_role
    FOREIGN KEY (role_id) REFERENCES role (id) ON DELETE CASCADE,
  CONSTRAINT uk_user_role_user_role UNIQUE (user_id, role_id)
);

CREATE TABLE resource_identifier (
  id            UUID         NOT NULL DEFAULT gen_random_uuid(),
  resource_type VARCHAR(32)  NOT NULL,
  resource_id   UUID         NOT NULL,
  system        TEXT         NOT NULL,   -- URI (RFC 3986); PostgreSQL no tiene tipo URI nativo
  value         VARCHAR(255) NOT NULL,
  use_code      VARCHAR(16)  NOT NULL DEFAULT 'official',
  period_start  TIMESTAMPTZ,
  period_end    TIMESTAMPTZ,
  active        BOOLEAN      NOT NULL DEFAULT TRUE,
  first_seen_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_resource_identifier PRIMARY KEY (id),
  CONSTRAINT chk_resource_identifier_type CHECK (
    resource_type IN (
      'institution', 'patient', 'physician', 'operation',
      'operating_room', 'instrument'
    )
  ),
  CONSTRAINT chk_resource_identifier_use_code CHECK (
    use_code IN ('usual', 'official', 'temp', 'secondary', 'old')
  ),
  CONSTRAINT chk_resource_identifier_period CHECK (
    period_end IS NULL OR period_end >= period_start
  )
);

-- UNIQUE parcial: solo identificadores activos (FHIR Identifier)
CREATE UNIQUE INDEX uk_resource_identifier_system_value_active
  ON resource_identifier (system, value)
  WHERE active = TRUE;

CREATE TABLE integration_client (
  id              UUID         NOT NULL DEFAULT gen_random_uuid(),
  client_id       VARCHAR(128) NOT NULL,
  name            VARCHAR(160) NOT NULL,
  secret_hash     TEXT         NOT NULL,
  scopes          JSONB        NOT NULL DEFAULT '[]'::jsonb,
  active          BOOLEAN      NOT NULL DEFAULT TRUE,
  last_used_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  institution_id  UUID         NOT NULL,
  CONSTRAINT pk_integration_client PRIMARY KEY (id),
  CONSTRAINT uk_integration_client_client_id UNIQUE (client_id),
  CONSTRAINT fk_integration_client_institution
    FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE CASCADE
);

-- -----------------------------------------------------------------------------
-- 5.4 Clínico-operativo
-- -----------------------------------------------------------------------------

CREATE TABLE patient (
  id             UUID         NOT NULL DEFAULT gen_random_uuid(),
  display_name   VARCHAR(160) NOT NULL,
  birth_date     DATE,
  active         BOOLEAN      NOT NULL DEFAULT TRUE,
  gender_id      UUID,
  institution_id UUID         NOT NULL,
  CONSTRAINT pk_patient PRIMARY KEY (id),
  CONSTRAINT fk_patient_gender
    FOREIGN KEY (gender_id) REFERENCES cat_gender (id) ON DELETE SET NULL,
  CONSTRAINT fk_patient_institution
    FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE RESTRICT
);

CREATE TABLE physician (
  id             UUID         NOT NULL DEFAULT gen_random_uuid(),
  name           VARCHAR(160) NOT NULL,
  active         BOOLEAN      NOT NULL DEFAULT TRUE,
  institution_id UUID         NOT NULL,
  CONSTRAINT pk_physician PRIMARY KEY (id),
  CONSTRAINT fk_physician_institution
    FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE RESTRICT
);

CREATE TABLE physician_specialty (
  id           UUID NOT NULL DEFAULT gen_random_uuid(),
  physician_id UUID NOT NULL,
  specialty_id UUID NOT NULL,
  CONSTRAINT pk_physician_specialty PRIMARY KEY (id),
  CONSTRAINT fk_physician_specialty_physician
    FOREIGN KEY (physician_id) REFERENCES physician (id) ON DELETE CASCADE,
  CONSTRAINT fk_physician_specialty_specialty
    FOREIGN KEY (specialty_id) REFERENCES cat_specialty (id) ON DELETE RESTRICT,
  CONSTRAINT uk_physician_specialty_pair UNIQUE (physician_id, specialty_id)
);

CREATE TABLE operating_room (
  id             UUID         NOT NULL DEFAULT gen_random_uuid(),
  code           VARCHAR(32)  NOT NULL,
  name           VARCHAR(120) NOT NULL,
  active         BOOLEAN      NOT NULL DEFAULT TRUE,
  institution_id UUID         NOT NULL,
  CONSTRAINT pk_operating_room PRIMARY KEY (id),
  CONSTRAINT fk_operating_room_institution
    FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE CASCADE,
  CONSTRAINT uk_operating_room_institution_code UNIQUE (institution_id, code)
);

CREATE TABLE capture_station (
  id     UUID         NOT NULL DEFAULT gen_random_uuid(),
  name   VARCHAR(120) NOT NULL,
  roi    JSONB,
  active BOOLEAN      NOT NULL DEFAULT TRUE,
  room_id UUID        NOT NULL,
  CONSTRAINT pk_capture_station PRIMARY KEY (id),
  CONSTRAINT fk_capture_station_room
    FOREIGN KEY (room_id) REFERENCES operating_room (id) ON DELETE CASCADE
);

CREATE TABLE operation (
  id                UUID        NOT NULL DEFAULT gen_random_uuid(),
  scheduled_at      TIMESTAMPTZ,
  started_at        TIMESTAMPTZ,
  ended_at          TIMESTAMPTZ,
  status_id         UUID        NOT NULL,
  procedure_type_id UUID,
  room_id           UUID,
  institution_id    UUID        NOT NULL,
  CONSTRAINT pk_operation PRIMARY KEY (id),
  CONSTRAINT fk_operation_status
    FOREIGN KEY (status_id) REFERENCES cat_operation_status (id) ON DELETE RESTRICT,
  CONSTRAINT fk_operation_procedure_type
    FOREIGN KEY (procedure_type_id) REFERENCES cat_procedure_type (id) ON DELETE SET NULL,
  CONSTRAINT fk_operation_room
    FOREIGN KEY (room_id) REFERENCES operating_room (id) ON DELETE SET NULL,
  CONSTRAINT fk_operation_institution
    FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE RESTRICT,
  CONSTRAINT chk_operation_timeline CHECK (
  ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at
  )
);

CREATE TABLE operation_patient (
  id           UUID NOT NULL DEFAULT gen_random_uuid(),
  operation_id UUID NOT NULL,
  patient_id   UUID NOT NULL,
  CONSTRAINT pk_operation_patient PRIMARY KEY (id),
  CONSTRAINT fk_operation_patient_operation
    FOREIGN KEY (operation_id) REFERENCES operation (id) ON DELETE CASCADE,
  CONSTRAINT fk_operation_patient_patient
    FOREIGN KEY (patient_id) REFERENCES patient (id) ON DELETE RESTRICT,
  CONSTRAINT uk_operation_patient_pair UNIQUE (operation_id, patient_id)
);

CREATE TABLE operation_physician (
  id               UUID NOT NULL DEFAULT gen_random_uuid(),
  operation_id     UUID NOT NULL,
  physician_id     UUID NOT NULL,
  surgical_role_id UUID NOT NULL,
  CONSTRAINT pk_operation_physician PRIMARY KEY (id),
  CONSTRAINT fk_operation_physician_operation
    FOREIGN KEY (operation_id) REFERENCES operation (id) ON DELETE CASCADE,
  CONSTRAINT fk_operation_physician_physician
    FOREIGN KEY (physician_id) REFERENCES physician (id) ON DELETE RESTRICT,
  CONSTRAINT fk_operation_physician_surgical_role
    FOREIGN KEY (surgical_role_id) REFERENCES cat_surgical_role (id) ON DELETE RESTRICT,
  CONSTRAINT uk_operation_physician_role UNIQUE (operation_id, physician_id, surgical_role_id)
);

-- -----------------------------------------------------------------------------
-- 5.5 Media (object storage: GCS / MinIO S3-compatible)
-- -----------------------------------------------------------------------------

CREATE TABLE media_asset (
  id                 UUID         NOT NULL DEFAULT gen_random_uuid(),
  gcs_uri            TEXT         NOT NULL,
  bucket             VARCHAR(128) NOT NULL,
  object_key         TEXT         NOT NULL,
  content_type       VARCHAR(128),
  kind               VARCHAR(32)  NOT NULL DEFAULT 'jpeg',
  storage_provider   VARCHAR(32)  NOT NULL DEFAULT 'gcs',
  sha256             CHAR(64),
  size_bytes         BIGINT,
  retention_until    TIMESTAMPTZ,
  purge_requested_at TIMESTAMPTZ,
  purged_at          TIMESTAMPTZ,
  blocked_at         TIMESTAMPTZ,
  created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_media_asset PRIMARY KEY (id),
  CONSTRAINT uk_media_asset_bucket_object_key UNIQUE (bucket, object_key),
  CONSTRAINT chk_media_asset_size_bytes CHECK (size_bytes IS NULL OR size_bytes >= 0),
  CONSTRAINT chk_media_asset_kind CHECK (
    kind IN ('jpeg', 'gif', 'weights', 'other')
  ),
  CONSTRAINT chk_media_asset_storage_provider CHECK (
    storage_provider IN ('gcs', 'minio', 'other')
  ),
  CONSTRAINT chk_media_asset_purge_order CHECK (
    purged_at IS NULL
    OR purge_requested_at IS NULL
    OR purged_at >= purge_requested_at
  )
);

-- -----------------------------------------------------------------------------
-- 5.6 Instrumental, kits y modelos
-- -----------------------------------------------------------------------------

CREATE TABLE instrument_family (
  id            UUID         NOT NULL DEFAULT gen_random_uuid(),
  code          VARCHAR(64)  NOT NULL,
  name          VARCHAR(160) NOT NULL,
  category_id   UUID         NOT NULL,
  identify_text TEXT,
  classify_text TEXT,
  function_text TEXT,
  active        BOOLEAN      NOT NULL DEFAULT TRUE,
  CONSTRAINT pk_instrument_family PRIMARY KEY (id),
  CONSTRAINT uk_instrument_family_code UNIQUE (code),
  CONSTRAINT fk_instrument_family_category
    FOREIGN KEY (category_id) REFERENCES cat_instrument_category (id) ON DELETE RESTRICT
);

CREATE TABLE family_example (
  id             UUID     NOT NULL DEFAULT gen_random_uuid(),
  family_id      UUID     NOT NULL,
  media_asset_id UUID,
  example_text   TEXT,
  sort_order     SMALLINT NOT NULL DEFAULT 0,
  CONSTRAINT pk_family_example PRIMARY KEY (id),
  CONSTRAINT fk_family_example_family
    FOREIGN KEY (family_id) REFERENCES instrument_family (id) ON DELETE CASCADE,
  CONSTRAINT fk_family_example_media
    FOREIGN KEY (media_asset_id) REFERENCES media_asset (id) ON DELETE SET NULL
);

CREATE TABLE instrument (
  id              UUID        NOT NULL DEFAULT gen_random_uuid(),
  internal_code   VARCHAR(64),
  family_id       UUID        NOT NULL,
  cycle_status_id UUID        NOT NULL,
  institution_id  UUID        NOT NULL,
  active          BOOLEAN     NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT pk_instrument PRIMARY KEY (id),
  CONSTRAINT fk_instrument_family
    FOREIGN KEY (family_id) REFERENCES instrument_family (id) ON DELETE RESTRICT,
  CONSTRAINT fk_instrument_cycle_status
    FOREIGN KEY (cycle_status_id) REFERENCES cat_instrument_cycle_status (id) ON DELETE RESTRICT,
  CONSTRAINT fk_instrument_institution
    FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX uk_instrument_institution_internal_code
  ON instrument (institution_id, internal_code)
  WHERE internal_code IS NOT NULL;

CREATE TABLE instrument_usage (
  id                UUID NOT NULL DEFAULT gen_random_uuid(),
  instrument_id     UUID NOT NULL,
  procedure_type_id UUID NOT NULL,
  context_id        UUID NOT NULL,
  notes             TEXT,
  active            BOOLEAN NOT NULL DEFAULT TRUE,
  CONSTRAINT pk_instrument_usage PRIMARY KEY (id),
  CONSTRAINT fk_instrument_usage_instrument
    FOREIGN KEY (instrument_id) REFERENCES instrument (id) ON DELETE CASCADE,
  CONSTRAINT fk_instrument_usage_procedure_type
    FOREIGN KEY (procedure_type_id) REFERENCES cat_procedure_type (id) ON DELETE RESTRICT,
  CONSTRAINT fk_instrument_usage_context
    FOREIGN KEY (context_id) REFERENCES cat_usage_context (id) ON DELETE RESTRICT,
  CONSTRAINT uk_instrument_usage_triple UNIQUE (instrument_id, procedure_type_id, context_id)
);

CREATE TABLE instrument_cycle_event (
  id              UUID        NOT NULL DEFAULT gen_random_uuid(),
  instrument_id   UUID        NOT NULL,
  cycle_status_id UUID        NOT NULL,
  occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  session_id      UUID,
  operation_id    UUID,
  notes           TEXT,
  CONSTRAINT pk_instrument_cycle_event PRIMARY KEY (id),
  CONSTRAINT fk_instrument_cycle_event_instrument
    FOREIGN KEY (instrument_id) REFERENCES instrument (id) ON DELETE CASCADE,
  CONSTRAINT fk_instrument_cycle_event_cycle_status
    FOREIGN KEY (cycle_status_id) REFERENCES cat_instrument_cycle_status (id) ON DELETE RESTRICT,
  CONSTRAINT fk_instrument_cycle_event_operation
    FOREIGN KEY (operation_id) REFERENCES operation (id) ON DELETE SET NULL
);

CREATE TABLE kit (
  id             UUID         NOT NULL DEFAULT gen_random_uuid(),
  name           VARCHAR(160) NOT NULL,
  version        SMALLINT     NOT NULL DEFAULT 1,
  active         BOOLEAN      NOT NULL DEFAULT TRUE,
  institution_id UUID         NOT NULL,
  created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_kit PRIMARY KEY (id),
  CONSTRAINT fk_kit_institution
    FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE CASCADE,
  CONSTRAINT uk_kit_institution_name_version UNIQUE (institution_id, name, version),
  CONSTRAINT chk_kit_version CHECK (version > 0)
);

CREATE TABLE kit_item (
  id       UUID     NOT NULL DEFAULT gen_random_uuid(),
  kit_id   UUID     NOT NULL,
  family_id UUID    NOT NULL,
  quantity SMALLINT NOT NULL,
  CONSTRAINT pk_kit_item PRIMARY KEY (id),
  CONSTRAINT fk_kit_item_kit
    FOREIGN KEY (kit_id) REFERENCES kit (id) ON DELETE CASCADE,
  CONSTRAINT fk_kit_item_family
    FOREIGN KEY (family_id) REFERENCES instrument_family (id) ON DELETE RESTRICT,
  CONSTRAINT uk_kit_item_kit_family UNIQUE (kit_id, family_id),
  CONSTRAINT chk_kit_item_quantity CHECK (quantity > 0)
);

CREATE TABLE procedure_kit (
  id                UUID         NOT NULL DEFAULT gen_random_uuid(),
  procedure_type_id UUID         NOT NULL,
  kit_id            UUID         NOT NULL,
  technique_label   VARCHAR(160),
  is_default        BOOLEAN      NOT NULL DEFAULT FALSE,
  active            BOOLEAN      NOT NULL DEFAULT TRUE,
  created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_procedure_kit PRIMARY KEY (id),
  CONSTRAINT fk_procedure_kit_procedure_type
    FOREIGN KEY (procedure_type_id) REFERENCES cat_procedure_type (id) ON DELETE CASCADE,
  CONSTRAINT fk_procedure_kit_kit
    FOREIGN KEY (kit_id) REFERENCES kit (id) ON DELETE RESTRICT,
  CONSTRAINT uk_procedure_kit_type_kit UNIQUE (procedure_type_id, kit_id)
);

CREATE UNIQUE INDEX uk_procedure_kit_default
  ON procedure_kit (procedure_type_id)
  WHERE is_default = TRUE AND active = TRUE;

CREATE TABLE procedure_phase (
  id                UUID     NOT NULL DEFAULT gen_random_uuid(),
  procedure_type_id UUID     NOT NULL,
  phase_id          UUID     NOT NULL,
  sort_order        SMALLINT NOT NULL,
  is_count_required BOOLEAN  NOT NULL DEFAULT TRUE,
  active            BOOLEAN  NOT NULL DEFAULT TRUE,
  CONSTRAINT pk_procedure_phase PRIMARY KEY (id),
  CONSTRAINT fk_procedure_phase_procedure_type
    FOREIGN KEY (procedure_type_id) REFERENCES cat_procedure_type (id) ON DELETE CASCADE,
  CONSTRAINT fk_procedure_phase_phase
    FOREIGN KEY (phase_id) REFERENCES cat_operation_phase (id) ON DELETE RESTRICT,
  CONSTRAINT uk_procedure_phase_type_phase UNIQUE (procedure_type_id, phase_id),
  CONSTRAINT uk_procedure_phase_type_sort UNIQUE (procedure_type_id, sort_order),
  CONSTRAINT chk_procedure_phase_sort_order CHECK (sort_order > 0)
);

CREATE TABLE instrument_reservation (
  id            UUID        NOT NULL DEFAULT gen_random_uuid(),
  operation_id  UUID        NOT NULL,
  instrument_id UUID        NOT NULL,
  reserved_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  released_at   TIMESTAMPTZ,
  active        BOOLEAN     NOT NULL DEFAULT TRUE,
  CONSTRAINT pk_instrument_reservation PRIMARY KEY (id),
  CONSTRAINT fk_instrument_reservation_operation
    FOREIGN KEY (operation_id) REFERENCES operation (id) ON DELETE CASCADE,
  CONSTRAINT fk_instrument_reservation_instrument
    FOREIGN KEY (instrument_id) REFERENCES instrument (id) ON DELETE RESTRICT,
  CONSTRAINT chk_instrument_reservation_released CHECK (
    released_at IS NULL OR released_at >= reserved_at
  )
);

CREATE UNIQUE INDEX uk_instrument_reservation_instrument_active
  ON instrument_reservation (instrument_id)
  WHERE active = TRUE;

CREATE TABLE yolo_model (
  id             UUID        NOT NULL DEFAULT gen_random_uuid(),
  version_tag    VARCHAR(32) NOT NULL,
  media_asset_id UUID,
  checksum       CHAR(64),
  active         BOOLEAN     NOT NULL DEFAULT FALSE,
  published_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT pk_yolo_model PRIMARY KEY (id),
  CONSTRAINT uk_yolo_model_version_tag UNIQUE (version_tag),
  CONSTRAINT fk_yolo_model_media
    FOREIGN KEY (media_asset_id) REFERENCES media_asset (id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX uk_yolo_model_active
  ON yolo_model (active)
  WHERE active = TRUE;

CREATE TABLE model_class (
  id            UUID     NOT NULL DEFAULT gen_random_uuid(),
  model_id      UUID     NOT NULL,
  family_id     UUID     NOT NULL,
  yolo_class_id SMALLINT NOT NULL,
  CONSTRAINT pk_model_class PRIMARY KEY (id),
  CONSTRAINT fk_model_class_model
    FOREIGN KEY (model_id) REFERENCES yolo_model (id) ON DELETE CASCADE,
  CONSTRAINT fk_model_class_family
    FOREIGN KEY (family_id) REFERENCES instrument_family (id) ON DELETE RESTRICT,
  CONSTRAINT uk_model_class_model_yolo_id UNIQUE (model_id, yolo_class_id),
  CONSTRAINT uk_model_class_model_family UNIQUE (model_id, family_id),
  CONSTRAINT chk_model_class_yolo_id CHECK (yolo_class_id >= 0)
);

-- -----------------------------------------------------------------------------
-- 5.7 Sesión de conteo y auditoría
-- -----------------------------------------------------------------------------

CREATE TABLE work_session (
  id                 UUID        NOT NULL DEFAULT gen_random_uuid(),
  started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at           TIMESTAMPTZ,
  status_id          UUID        NOT NULL,
  user_id            UUID        NOT NULL,
  closed_by_user_id  UUID,
  operation_id       UUID,
  station_id         UUID,
  kit_id             UUID,
  current_phase_id   UUID,
  phase_changed_at   TIMESTAMPTZ,
  atypical_session   BOOLEAN     NOT NULL DEFAULT FALSE,
  extended_retention BOOLEAN     NOT NULL DEFAULT FALSE,
  retention_until    TIMESTAMPTZ,
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT pk_work_session PRIMARY KEY (id),
  CONSTRAINT fk_work_session_status
    FOREIGN KEY (status_id) REFERENCES cat_session_status (id) ON DELETE RESTRICT,
  CONSTRAINT fk_work_session_user
    FOREIGN KEY (user_id) REFERENCES "user" (id) ON DELETE RESTRICT,
  CONSTRAINT fk_work_session_closed_by
    FOREIGN KEY (closed_by_user_id) REFERENCES "user" (id) ON DELETE SET NULL,
  CONSTRAINT fk_work_session_operation
    FOREIGN KEY (operation_id) REFERENCES operation (id) ON DELETE RESTRICT,
  CONSTRAINT fk_work_session_station
    FOREIGN KEY (station_id) REFERENCES capture_station (id) ON DELETE SET NULL,
  CONSTRAINT fk_work_session_kit
    FOREIGN KEY (kit_id) REFERENCES kit (id) ON DELETE SET NULL,
  CONSTRAINT fk_work_session_current_phase
    FOREIGN KEY (current_phase_id) REFERENCES cat_operation_phase (id) ON DELETE SET NULL,
  CONSTRAINT chk_work_session_ended CHECK (
    ended_at IS NULL OR ended_at >= started_at
  )
);

CREATE TABLE expected_inventory (
  id                UUID         NOT NULL DEFAULT gen_random_uuid(),
  family_id         UUID         NOT NULL,
  expected_quantity SMALLINT     NOT NULL,
  session_id        UUID         NOT NULL,
  source            VARCHAR(32)  NOT NULL DEFAULT 'kit_snapshot',
  CONSTRAINT pk_expected_inventory PRIMARY KEY (id),
  CONSTRAINT fk_expected_inventory_family
    FOREIGN KEY (family_id) REFERENCES instrument_family (id) ON DELETE RESTRICT,
  CONSTRAINT fk_expected_inventory_session
    FOREIGN KEY (session_id) REFERENCES work_session (id) ON DELETE CASCADE,
  CONSTRAINT uk_expected_inventory_session_family UNIQUE (session_id, family_id),
  CONSTRAINT chk_expected_inventory_quantity CHECK (expected_quantity >= 0),
  CONSTRAINT chk_expected_inventory_source CHECK (
    source IN ('kit_snapshot', 'manual')
  )
);

CREATE TABLE count_event (
  id                UUID        NOT NULL DEFAULT gen_random_uuid(),
  event_type_id     UUID        NOT NULL,
  client_event_id   UUID,
  family_id         UUID,
  expected_quantity SMALLINT,
  detected_quantity SMALLINT,
  payload           JSONB       NOT NULL DEFAULT '{}'::jsonb,
  occurred_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  session_id        UUID        NOT NULL,
  user_id           UUID,
  CONSTRAINT pk_count_event PRIMARY KEY (id),
  CONSTRAINT fk_count_event_event_type
    FOREIGN KEY (event_type_id) REFERENCES cat_event_type (id) ON DELETE RESTRICT,
  CONSTRAINT fk_count_event_family
    FOREIGN KEY (family_id) REFERENCES instrument_family (id) ON DELETE SET NULL,
  CONSTRAINT fk_count_event_session
    FOREIGN KEY (session_id) REFERENCES work_session (id) ON DELETE CASCADE,
  CONSTRAINT fk_count_event_user
    FOREIGN KEY (user_id) REFERENCES "user" (id) ON DELETE SET NULL,
  CONSTRAINT uk_count_event_client_event UNIQUE (client_event_id),
  CONSTRAINT chk_count_event_quantities CHECK (
    (expected_quantity IS NULL OR expected_quantity >= 0)
    AND (detected_quantity IS NULL OR detected_quantity >= 0)
  )
);

CREATE TABLE discrepancy (
  id                UUID     NOT NULL DEFAULT gen_random_uuid(),
  description       TEXT     NOT NULL,
  resolved          BOOLEAN  NOT NULL DEFAULT FALSE,
  resolved_at       TIMESTAMPTZ,
  reason_id         UUID,
  family_id         UUID,
  expected_quantity SMALLINT,
  detected_quantity SMALLINT,
  session_id        UUID     NOT NULL,
  origin_event_id   UUID,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT pk_discrepancy PRIMARY KEY (id),
  CONSTRAINT fk_discrepancy_reason
    FOREIGN KEY (reason_id) REFERENCES cat_discrepancy_reason (id) ON DELETE SET NULL,
  CONSTRAINT fk_discrepancy_family
    FOREIGN KEY (family_id) REFERENCES instrument_family (id) ON DELETE SET NULL,
  CONSTRAINT fk_discrepancy_session
    FOREIGN KEY (session_id) REFERENCES work_session (id) ON DELETE CASCADE,
  CONSTRAINT fk_discrepancy_origin_event
    FOREIGN KEY (origin_event_id) REFERENCES count_event (id) ON DELETE SET NULL,
  CONSTRAINT chk_discrepancy_resolved_at CHECK (
    resolved = FALSE OR resolved_at IS NOT NULL
  ),
  CONSTRAINT chk_discrepancy_quantities CHECK (
    (expected_quantity IS NULL OR expected_quantity >= 0)
    AND (detected_quantity IS NULL OR detected_quantity >= 0)
  )
);

CREATE TABLE human_correction (
  id             UUID        NOT NULL DEFAULT gen_random_uuid(),
  justification  TEXT        NOT NULL,
  recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  count_event_id UUID        NOT NULL,
  user_id        UUID,
  CONSTRAINT pk_human_correction PRIMARY KEY (id),
  CONSTRAINT uk_human_correction_count_event UNIQUE (count_event_id),
  CONSTRAINT fk_human_correction_count_event
    FOREIGN KEY (count_event_id) REFERENCES count_event (id) ON DELETE CASCADE,
  CONSTRAINT fk_human_correction_user
    FOREIGN KEY (user_id) REFERENCES "user" (id) ON DELETE SET NULL
);

-- -----------------------------------------------------------------------------
-- 5.7b Privacy, processing agreements, access audit, ARCO
-- -----------------------------------------------------------------------------

CREATE TABLE privacy_notice_version (
  id              UUID         NOT NULL DEFAULT gen_random_uuid(),
  version         VARCHAR(32)  NOT NULL,
  effective_at    TIMESTAMPTZ  NOT NULL,
  document_uri    TEXT         NOT NULL,
  content_sha256  CHAR(64),
  active          BOOLEAN      NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_privacy_notice_version PRIMARY KEY (id),
  CONSTRAINT uk_privacy_notice_version UNIQUE (version)
);

CREATE TABLE session_processing_agreement (
  id                         UUID        NOT NULL DEFAULT gen_random_uuid(),
  session_id                 UUID        NOT NULL,
  privacy_notice_version_id  UUID        NOT NULL,
  purpose_quality_ops        BOOLEAN     NOT NULL DEFAULT TRUE,
  purpose_model_improvement  BOOLEAN     NOT NULL DEFAULT FALSE,
  agreed_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  agreed_by_user_id          UUID,
  CONSTRAINT pk_session_processing_agreement PRIMARY KEY (id),
  CONSTRAINT uk_session_processing_agreement_session UNIQUE (session_id),
  CONSTRAINT fk_session_processing_agreement_session
    FOREIGN KEY (session_id) REFERENCES work_session (id) ON DELETE CASCADE,
  CONSTRAINT fk_session_processing_agreement_notice
    FOREIGN KEY (privacy_notice_version_id) REFERENCES privacy_notice_version (id) ON DELETE RESTRICT,
  CONSTRAINT fk_session_processing_agreement_user
    FOREIGN KEY (agreed_by_user_id) REFERENCES "user" (id) ON DELETE SET NULL,
  CONSTRAINT chk_session_processing_agreement_quality_ops CHECK (
    purpose_quality_ops = TRUE
  )
);

CREATE TABLE access_audit (
  id               UUID         NOT NULL DEFAULT gen_random_uuid(),
  occurred_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  actor_type       VARCHAR(16)  NOT NULL,
  actor_user_id    UUID,
  actor_client_id  UUID,
  action           VARCHAR(64)  NOT NULL,
  resource_type    VARCHAR(64)  NOT NULL,
  resource_id      UUID         NOT NULL,
  institution_id   UUID,
  outcome          VARCHAR(16)  NOT NULL DEFAULT 'success',
  correlation_id   VARCHAR(64),
  ip               INET,
  CONSTRAINT pk_access_audit PRIMARY KEY (id),
  CONSTRAINT fk_access_audit_user
    FOREIGN KEY (actor_user_id) REFERENCES "user" (id) ON DELETE SET NULL,
  CONSTRAINT fk_access_audit_client
    FOREIGN KEY (actor_client_id) REFERENCES integration_client (id) ON DELETE SET NULL,
  CONSTRAINT fk_access_audit_institution
    FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE SET NULL,
  CONSTRAINT chk_access_audit_actor_type CHECK (
    actor_type IN ('user', 'client')
  ),
  CONSTRAINT chk_access_audit_actor_pair CHECK (
    (actor_type = 'user' AND actor_user_id IS NOT NULL AND actor_client_id IS NULL)
    OR (actor_type = 'client' AND actor_client_id IS NOT NULL AND actor_user_id IS NULL)
  ),
  CONSTRAINT chk_access_audit_outcome CHECK (
    outcome IN ('success', 'denied')
  )
);

CREATE TABLE privacy_request (
  id                  UUID         NOT NULL DEFAULT gen_random_uuid(),
  request_type        VARCHAR(16)  NOT NULL,
  status              VARCHAR(32)  NOT NULL DEFAULT 'received',
  channel             VARCHAR(32),
  subject_patient_id  UUID         NOT NULL,
  requested_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
  due_at              TIMESTAMPTZ,
  handled_by_user_id  UUID,
  resolution_notes    TEXT,
  completed_at        TIMESTAMPTZ,
  created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_privacy_request PRIMARY KEY (id),
  CONSTRAINT fk_privacy_request_patient
    FOREIGN KEY (subject_patient_id) REFERENCES patient (id) ON DELETE RESTRICT,
  CONSTRAINT fk_privacy_request_handler
    FOREIGN KEY (handled_by_user_id) REFERENCES "user" (id) ON DELETE SET NULL,
  CONSTRAINT chk_privacy_request_type CHECK (
    request_type IN ('access', 'rectify', 'cancel', 'oppose')
  ),
  CONSTRAINT chk_privacy_request_status CHECK (
    status IN ('received', 'in_progress', 'completed', 'denied')
  ),
  CONSTRAINT chk_privacy_request_completed CHECK (
    status NOT IN ('completed', 'denied')
    OR completed_at IS NOT NULL
  )
);

-- -----------------------------------------------------------------------------
-- 5.8 FK diferida (tabla creada antes que work_session)
-- -----------------------------------------------------------------------------

ALTER TABLE instrument_cycle_event
  ADD CONSTRAINT fk_instrument_cycle_event_session
  FOREIGN KEY (session_id) REFERENCES work_session (id) ON DELETE SET NULL;

-- -----------------------------------------------------------------------------
-- 5.9 Índices de consulta (FK y filtros frecuentes)
-- -----------------------------------------------------------------------------

CREATE INDEX idx_role_institution ON role (institution_id);
CREATE INDEX idx_user_institution ON "user" (institution_id);
CREATE INDEX idx_user_role_user ON user_role (user_id);
CREATE INDEX idx_user_role_role ON user_role (role_id);

CREATE INDEX idx_resource_identifier_lookup
  ON resource_identifier (resource_type, resource_id);

CREATE INDEX idx_patient_institution ON patient (institution_id);
CREATE INDEX idx_patient_gender ON patient (gender_id);
CREATE INDEX idx_physician_institution ON physician (institution_id);
CREATE INDEX idx_operating_room_institution ON operating_room (institution_id);
CREATE INDEX idx_capture_station_room ON capture_station (room_id);

CREATE INDEX idx_operation_institution ON operation (institution_id);
CREATE INDEX idx_operation_status ON operation (status_id);
CREATE INDEX idx_operation_room ON operation (room_id);
CREATE INDEX idx_operation_procedure_type ON operation (procedure_type_id);
CREATE INDEX idx_operation_patient_operation ON operation_patient (operation_id);
CREATE INDEX idx_operation_patient_patient ON operation_patient (patient_id);
CREATE INDEX idx_operation_physician_operation ON operation_physician (operation_id);

CREATE INDEX idx_instrument_family_category ON instrument_family (category_id);
CREATE INDEX idx_instrument_family ON instrument (family_id);
CREATE INDEX idx_instrument_cycle_status ON instrument (cycle_status_id);
CREATE INDEX idx_instrument_institution ON instrument (institution_id);
CREATE INDEX idx_instrument_usage_instrument ON instrument_usage (instrument_id);
CREATE INDEX idx_instrument_cycle_event_instrument ON instrument_cycle_event (instrument_id);
CREATE INDEX idx_instrument_cycle_event_session ON instrument_cycle_event (session_id);
CREATE INDEX idx_instrument_reservation_operation ON instrument_reservation (operation_id);

CREATE INDEX idx_kit_institution ON kit (institution_id);
CREATE INDEX idx_kit_item_kit ON kit_item (kit_id);
CREATE INDEX idx_procedure_kit_procedure_type ON procedure_kit (procedure_type_id);
CREATE INDEX idx_procedure_kit_kit ON procedure_kit (kit_id);
CREATE INDEX idx_procedure_phase_procedure_type ON procedure_phase (procedure_type_id);
CREATE INDEX idx_procedure_phase_phase ON procedure_phase (phase_id);

CREATE INDEX idx_work_session_user ON work_session (user_id);
CREATE INDEX idx_work_session_operation ON work_session (operation_id);
CREATE INDEX idx_work_session_status ON work_session (status_id);
CREATE INDEX idx_work_session_station ON work_session (station_id);
CREATE INDEX idx_work_session_current_phase ON work_session (current_phase_id);
CREATE INDEX idx_expected_inventory_session ON expected_inventory (session_id);
CREATE INDEX idx_expected_inventory_family ON expected_inventory (family_id);
CREATE INDEX idx_count_event_session ON count_event (session_id);
CREATE INDEX idx_count_event_event_type ON count_event (event_type_id);
CREATE INDEX idx_count_event_family ON count_event (family_id);
CREATE INDEX idx_count_event_occurred_at ON count_event (occurred_at);
CREATE INDEX idx_discrepancy_session ON discrepancy (session_id);
CREATE INDEX idx_discrepancy_origin_event ON discrepancy (origin_event_id);
CREATE INDEX idx_discrepancy_family ON discrepancy (family_id);
CREATE INDEX idx_discrepancy_resolved ON discrepancy (resolved);

CREATE INDEX idx_integration_client_institution ON integration_client (institution_id);
CREATE INDEX idx_session_processing_agreement_notice
  ON session_processing_agreement (privacy_notice_version_id);
CREATE INDEX idx_access_audit_occurred_at ON access_audit (occurred_at);
CREATE INDEX idx_access_audit_actor_user ON access_audit (actor_user_id);
CREATE INDEX idx_access_audit_actor_client ON access_audit (actor_client_id);
CREATE INDEX idx_access_audit_resource ON access_audit (resource_type, resource_id);
CREATE INDEX idx_privacy_request_patient ON privacy_request (subject_patient_id);
CREATE INDEX idx_privacy_request_status ON privacy_request (status);
CREATE INDEX idx_media_asset_retention_until
  ON media_asset (retention_until)
  WHERE purged_at IS NULL;
CREATE INDEX idx_media_asset_purge_requested
  ON media_asset (purge_requested_at)
  WHERE purged_at IS NULL AND purge_requested_at IS NOT NULL;
