-- =============================================================================
-- 014 — MS-07: sincronización incremental, idempotencia de operaciones, clientes máquina e intercambio FHIR
--
-- Idempotente (se puede aplicar varias veces). No modifica ni borra datos existentes.
--
-- 1. sync_change: registro de cambios (change log) llenado por triggers en la MISMA transacción que el cambio.
--    Cada fila lleva el xid8 de su transacción: el lector solo entrega filas de transacciones ya terminadas
--    (txid < pg_snapshot_xmin(pg_current_snapshot())) en orden (txid, seq). Así una transacción larga que
--    confirma tarde nunca queda "detrás" del cursor de un cliente: no hay omisiones ni duplicados por
--    paginación concurrente, sin depender de la fecha del cliente.
-- 2. sync_resource_version: versión monotónica por recurso (UPSERT ... RETURNING bajo bloqueo de fila).
-- 3. sync_operation: claves de idempotencia de las operaciones enviadas por clientes (por usuario o cliente).
-- 4. integration_client (DS08, ya existía): fecha de rotación del secreto y autor.
-- 5. integration_endpoint / integration_delivery: destinos FHIR autorizados por institución y registro de
--    entregas e intentos (nunca se escribe en la base de datos de un HIS).
-- =============================================================================

CREATE TABLE IF NOT EXISTS sync_change (
  seq            BIGSERIAL    NOT NULL,
  txid           XID8         NOT NULL DEFAULT pg_current_xact_id(),
  institution_id UUID,                       -- NULL = catálogo compartido (familias)
  resource_type  VARCHAR(40)  NOT NULL,
  resource_id    UUID         NOT NULL,
  operation      VARCHAR(12)  NOT NULL,
  version        BIGINT       NOT NULL,
  owner_user_id  UUID,                       -- work_session: Operador asignado (visibilidad del Operador)
  changed_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_sync_change PRIMARY KEY (seq),
  CONSTRAINT chk_sync_change_operation CHECK (operation IN ('create', 'update', 'deactivate', 'delete'))
);
CREATE INDEX IF NOT EXISTS idx_sync_change_position ON sync_change (txid, seq);
CREATE INDEX IF NOT EXISTS idx_sync_change_institution ON sync_change (institution_id, txid, seq);
CREATE INDEX IF NOT EXISTS idx_sync_change_resource ON sync_change (resource_type, resource_id, seq DESC);

CREATE TABLE IF NOT EXISTS sync_resource_version (
  resource_type VARCHAR(40) NOT NULL,
  resource_id   UUID        NOT NULL,
  version       BIGINT      NOT NULL,
  CONSTRAINT pk_sync_resource_version PRIMARY KEY (resource_type, resource_id)
);

-- Tipos de recurso ya sembrados (backfill de una sola vez por tipo).
CREATE TABLE IF NOT EXISTS sync_seed (
  resource_type VARCHAR(40) NOT NULL,
  seeded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT pk_sync_seed PRIMARY KEY (resource_type)
);

CREATE OR REPLACE FUNCTION sync_record_change(p_type TEXT, p_id UUID, p_institution UUID, p_operation TEXT,
                                              p_owner UUID DEFAULT NULL) RETURNS VOID AS $$
DECLARE
  v_version BIGINT;
BEGIN
  INSERT INTO sync_resource_version (resource_type, resource_id, version) VALUES (p_type, p_id, 1)
  ON CONFLICT (resource_type, resource_id) DO UPDATE SET version = sync_resource_version.version + 1
  RETURNING version INTO v_version;
  INSERT INTO sync_change (institution_id, resource_type, resource_id, operation, version, owner_user_id)
  VALUES (p_institution, p_type, p_id, p_operation, v_version, p_owner);
END;
$$ LANGUAGE plpgsql;

-- Trigger genérico: institución y tipo según la tabla. UPDATE sin cambios reales no se registra.
CREATE OR REPLACE FUNCTION sync_capture_change() RETURNS TRIGGER AS $$
DECLARE
  v_row         RECORD;
  v_type        TEXT := TG_ARGV[0];
  v_institution UUID;
  v_owner       UUID;
  v_operation   TEXT;
  v_old_active  BOOLEAN;
  v_new_active  BOOLEAN;
BEGIN
  IF TG_OP = 'DELETE' THEN
    v_row := OLD;
  ELSE
    v_row := NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN
    RETURN NULL;
  END IF;

  IF TG_TABLE_NAME = 'instrument_family' THEN
    v_institution := NULL;
  ELSIF TG_TABLE_NAME = 'capture_station' THEN
    SELECT r.institution_id INTO v_institution FROM operating_room r WHERE r.id = v_row.room_id;
  ELSIF TG_TABLE_NAME = 'work_session' THEN
    SELECT o.institution_id INTO v_institution FROM operation o WHERE o.id = v_row.operation_id;
    IF v_institution IS NULL THEN
      SELECT u.institution_id INTO v_institution FROM "user" u WHERE u.id = v_row.user_id;
    END IF;
    v_owner := v_row.user_id;
  ELSE
    v_institution := v_row.institution_id;
  END IF;

  IF TG_OP = 'INSERT' THEN
    v_operation := 'create';
  ELSIF TG_OP = 'DELETE' THEN
    v_operation := 'delete';
  ELSE
    v_operation := 'update';
    IF TG_TABLE_NAME = 'surgical_tray' THEN
      IF to_jsonb(OLD) ->> 'status' <> 'retired' AND to_jsonb(NEW) ->> 'status' = 'retired' THEN
        v_operation := 'deactivate';
      END IF;
    ELSIF to_jsonb(NEW) ? 'active' THEN
      v_old_active := (to_jsonb(OLD) ->> 'active')::BOOLEAN;
      v_new_active := (to_jsonb(NEW) ->> 'active')::BOOLEAN;
      IF v_old_active AND NOT v_new_active THEN
        v_operation := 'deactivate';
      END IF;
    END IF;
  END IF;

  PERFORM sync_record_change(v_type, v_row.id, v_institution, v_operation, v_owner);
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Recursos sincronizables y su tabla de origen. Se crean solo si la tabla existe.
DO $$
DECLARE
  v_pair TEXT[];
  v_pairs TEXT[][] := ARRAY[
    ARRAY['instrument_family', 'instrument_family'],
    ARRAY['kit', 'kit'],
    ARRAY['instrument', 'instrument'],
    ARRAY['operating_room', 'operating_room'],
    ARRAY['capture_station', 'capture_station'],
    ARRAY['capture_camera', 'capture_camera'],
    ARRAY['physician', 'physician'],
    ARRAY['work_session', 'work_session']
  ];
BEGIN
  FOREACH v_pair SLICE 1 IN ARRAY v_pairs LOOP
    IF to_regclass(v_pair[1]) IS NOT NULL THEN
      EXECUTE format('DROP TRIGGER IF EXISTS trg_sync_%s ON %I', v_pair[1], v_pair[1]);
      EXECUTE format('CREATE TRIGGER trg_sync_%s AFTER INSERT OR UPDATE OR DELETE ON %I '
                     'FOR EACH ROW EXECUTE FUNCTION sync_capture_change(%L)', v_pair[1], v_pair[1], v_pair[2]);
    END IF;
  END LOOP;
END $$;

-- Backfill: una entrada "create" (o "deactivate" si ya está inactivo) por cada fila existente, una sola vez
-- por tipo, para que la primera sincronización de un cliente (sin cursor) reciba el estado completo.
CREATE OR REPLACE FUNCTION sync_seed_resource(p_type TEXT, p_sql TEXT) RETURNS VOID AS $$
DECLARE
  v_row RECORD;
BEGIN
  IF EXISTS (SELECT 1 FROM sync_seed WHERE resource_type = p_type) THEN
    RETURN;
  END IF;
  FOR v_row IN EXECUTE p_sql LOOP
    PERFORM sync_record_change(p_type, v_row.id, v_row.institution_id,
                               CASE WHEN v_row.active THEN 'create' ELSE 'deactivate' END, v_row.owner_id);
  END LOOP;
  INSERT INTO sync_seed (resource_type) VALUES (p_type);
END;
$$ LANGUAGE plpgsql;

SELECT sync_seed_resource('instrument_family', 'SELECT id, NULL::uuid AS institution_id, active, NULL::uuid AS owner_id FROM instrument_family ORDER BY id');
SELECT sync_seed_resource('kit', 'SELECT id, institution_id, active, NULL::uuid AS owner_id FROM kit ORDER BY created_at, id');
SELECT sync_seed_resource('instrument', 'SELECT id, institution_id, active, NULL::uuid AS owner_id FROM instrument ORDER BY id');
SELECT sync_seed_resource('operating_room', 'SELECT id, institution_id, active, NULL::uuid AS owner_id FROM operating_room ORDER BY id');
SELECT sync_seed_resource('capture_station', 'SELECT s.id, r.institution_id, s.active, NULL::uuid AS owner_id FROM capture_station s JOIN operating_room r ON r.id = s.room_id ORDER BY s.id');
SELECT sync_seed_resource('physician', 'SELECT id, institution_id, active, NULL::uuid AS owner_id FROM physician ORDER BY id');
SELECT sync_seed_resource('work_session', 'SELECT w.id, COALESCE(o.institution_id, u.institution_id) AS institution_id, TRUE AS active, w.user_id AS owner_id FROM work_session w LEFT JOIN operation o ON o.id = w.operation_id LEFT JOIN "user" u ON u.id = w.user_id ORDER BY w.id');
DO $$
BEGIN
  IF to_regclass('capture_camera') IS NOT NULL THEN
    PERFORM sync_seed_resource('capture_camera', 'SELECT id, institution_id, active, NULL::uuid AS owner_id FROM capture_camera ORDER BY id');
  END IF;
END $$;

-- -----------------------------------------------------------------------------------------------------------
-- Idempotencia de operaciones enviadas por clientes (escritorio / Android / máquina)
-- -----------------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sync_operation (
  principal_type  VARCHAR(8)   NOT NULL,      -- user | client
  principal_id    UUID         NOT NULL,
  idempotency_key VARCHAR(128) NOT NULL,
  institution_id  UUID         NOT NULL,
  operation_type  VARCHAR(40)  NOT NULL,
  request_hash    CHAR(64)     NOT NULL,      -- SHA-256 del tipo + objetivo + cuerpo canónico
  correlation_id  VARCHAR(64)  NOT NULL,      -- enviado al servicio dueño; permite reconciliar un resultado incierto
  status          VARCHAR(12)  NOT NULL DEFAULT 'in_progress',
  response_status SMALLINT,
  response_body   JSONB,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  expires_at      TIMESTAMPTZ  NOT NULL DEFAULT (now() + interval '7 days'),
  CONSTRAINT pk_sync_operation PRIMARY KEY (principal_type, principal_id, idempotency_key),
  CONSTRAINT chk_sync_operation_principal CHECK (principal_type IN ('user', 'client')),
  CONSTRAINT chk_sync_operation_status CHECK (status IN ('in_progress', 'completed', 'unknown'))
);
CREATE INDEX IF NOT EXISTS idx_sync_operation_expires ON sync_operation (expires_at);

-- -----------------------------------------------------------------------------------------------------------
-- Clientes máquina (DS08): la tabla ya existe en 001; se agregan rotación y autor
-- -----------------------------------------------------------------------------------------------------------
ALTER TABLE integration_client ADD COLUMN IF NOT EXISTS secret_rotated_at TIMESTAMPTZ;
ALTER TABLE integration_client ADD COLUMN IF NOT EXISTS created_by_user_id UUID;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_integration_client_created_by') THEN
    ALTER TABLE integration_client ADD CONSTRAINT fk_integration_client_created_by
      FOREIGN KEY (created_by_user_id) REFERENCES "user" (id) ON DELETE SET NULL;
  END IF;
END $$;

-- -----------------------------------------------------------------------------------------------------------
-- Destinos FHIR autorizados y registro de entregas
-- -----------------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS integration_endpoint (
  id             UUID         NOT NULL DEFAULT gen_random_uuid(),
  institution_id UUID         NOT NULL,
  name           VARCHAR(160) NOT NULL,
  base_url       TEXT         NOT NULL,      -- servidor FHIR R4 (solo hosts permitidos por configuración)
  resource_types JSONB        NOT NULL DEFAULT '["Encounter","Location","Practitioner","Organization"]'::jsonb,
  active         BOOLEAN      NOT NULL DEFAULT TRUE,
  created_by_user_id UUID,
  created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_integration_endpoint PRIMARY KEY (id),
  CONSTRAINT fk_integration_endpoint_institution FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE CASCADE,
  CONSTRAINT uk_integration_endpoint_name UNIQUE (institution_id, name)
);

CREATE TABLE IF NOT EXISTS integration_delivery (
  id              UUID         NOT NULL DEFAULT gen_random_uuid(),
  endpoint_id     UUID         NOT NULL,
  institution_id  UUID         NOT NULL,
  resource_type   VARCHAR(40)  NOT NULL,     -- recurso FHIR raíz (Encounter)
  resource_id     UUID         NOT NULL,     -- id PEF del recurso de origen (operation)
  idempotency_key VARCHAR(128) NOT NULL,
  request_hash    CHAR(64)     NOT NULL,
  status          VARCHAR(12)  NOT NULL DEFAULT 'pending',
  attempts        INTEGER      NOT NULL DEFAULT 0,
  next_attempt_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
  last_status_code SMALLINT,
  last_error      TEXT,
  response_excerpt TEXT,
  requested_by_user_id UUID,
  requested_by_client_id UUID,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  sent_at         TIMESTAMPTZ,
  CONSTRAINT pk_integration_delivery PRIMARY KEY (id),
  CONSTRAINT fk_integration_delivery_endpoint FOREIGN KEY (endpoint_id) REFERENCES integration_endpoint (id) ON DELETE CASCADE,
  CONSTRAINT uk_integration_delivery_key UNIQUE (endpoint_id, idempotency_key),
  CONSTRAINT chk_integration_delivery_status CHECK (status IN ('pending', 'sent', 'failed'))
);
CREATE INDEX IF NOT EXISTS idx_integration_delivery_due ON integration_delivery (status, next_attempt_at);

CREATE TABLE IF NOT EXISTS integration_delivery_attempt (
  id           BIGSERIAL    NOT NULL,
  delivery_id  UUID         NOT NULL,
  attempted_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
  status_code  SMALLINT,
  outcome      VARCHAR(12)  NOT NULL,        -- sent | retry | failed
  error        TEXT,
  CONSTRAINT pk_integration_delivery_attempt PRIMARY KEY (id),
  CONSTRAINT fk_integration_delivery_attempt FOREIGN KEY (delivery_id) REFERENCES integration_delivery (id) ON DELETE CASCADE
);
