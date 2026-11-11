-- =============================================================================
-- 015 — MS-08: charola física (surgical_tray) como entidad trazable
--
-- Idempotente. No modifica kits, sesiones ni cantidades existentes.
--
-- Diferencia kit / charola:
--   kit           = definición (composición versionada: cada cambio crea una versión nueva, la anterior se
--                   desactiva). Ya existía.
--   surgical_tray = objeto físico individual con su propio código (etiqueta), institución, estado y, si se
--                   conoce, la versión del kit con que está configurado. Dos charolas con el mismo kit son
--                   dos filas distintas.
--
-- Referencias históricas: work_session.tray_id es opcional y nunca cambia las cantidades esperadas de la
-- sesión (siguen congeladas al programarla). Si el kit de la charola se reemplaza después por una versión
-- nueva, la charola conserva la versión con que fue configurada hasta que el Supervisor la cambie;
-- surgical_tray_history guarda cada cambio (quién, cuándo, antes y después).
--
-- Estados administrativos (no clínicos): active | out_of_service | retired. El ciclo operativo (en uso,
-- en esterilización...) NO está definido por los RF: queda como decisión pendiente (no se infiere).
-- =============================================================================

CREATE TABLE IF NOT EXISTS surgical_tray (
  id                 UUID         NOT NULL DEFAULT gen_random_uuid(),
  institution_id     UUID         NOT NULL,
  code               VARCHAR(64)  NOT NULL,     -- etiqueta física (única por institución)
  name               VARCHAR(160),
  kit_id             UUID,                      -- versión del kit con que está configurada (opcional)
  status             VARCHAR(16)  NOT NULL DEFAULT 'active',
  version            INTEGER      NOT NULL DEFAULT 1,   -- control de concurrencia optimista
  notes              VARCHAR(500),
  created_by_user_id UUID,
  updated_by_user_id UUID,
  created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_surgical_tray PRIMARY KEY (id),
  CONSTRAINT fk_surgical_tray_institution FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE RESTRICT,
  CONSTRAINT fk_surgical_tray_kit FOREIGN KEY (kit_id) REFERENCES kit (id) ON DELETE RESTRICT,
  CONSTRAINT uk_surgical_tray_code UNIQUE (institution_id, code),
  CONSTRAINT chk_surgical_tray_status CHECK (status IN ('active', 'out_of_service', 'retired')),
  CONSTRAINT chk_surgical_tray_version CHECK (version >= 1)
);
CREATE INDEX IF NOT EXISTS idx_surgical_tray_kit ON surgical_tray (kit_id);

-- El kit de la charola debe ser de la misma institución (también para escrituras fuera de la API).
CREATE OR REPLACE FUNCTION surgical_tray_same_institution() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.kit_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM kit k WHERE k.id = NEW.kit_id AND k.institution_id = NEW.institution_id) THEN
    RAISE EXCEPTION 'surgical_tray kit must belong to the tray institution' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_surgical_tray_same_institution ON surgical_tray;
CREATE TRIGGER trg_surgical_tray_same_institution BEFORE INSERT OR UPDATE ON surgical_tray
  FOR EACH ROW EXECUTE FUNCTION surgical_tray_same_institution();

CREATE TABLE IF NOT EXISTS surgical_tray_history (
  id          BIGSERIAL    NOT NULL,
  tray_id     UUID         NOT NULL,
  version     INTEGER      NOT NULL,
  changed_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  changed_by_user_id UUID,
  change_type VARCHAR(12)  NOT NULL,     -- create | update | status
  before      JSONB,
  after       JSONB        NOT NULL,
  CONSTRAINT pk_surgical_tray_history PRIMARY KEY (id),
  CONSTRAINT fk_surgical_tray_history_tray FOREIGN KEY (tray_id) REFERENCES surgical_tray (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_surgical_tray_history_tray ON surgical_tray_history (tray_id, id);

-- Historial automático (también ante escrituras directas): solo campos de negocio.
CREATE OR REPLACE FUNCTION surgical_tray_record_history() RETURNS TRIGGER AS $$
DECLARE
  v_after  JSONB := jsonb_build_object('code', NEW.code, 'name', NEW.name, 'kit_id', NEW.kit_id,
                                       'status', NEW.status, 'notes', NEW.notes);
  v_before JSONB;
  v_type   TEXT := 'create';
BEGIN
  IF TG_OP = 'UPDATE' THEN
    v_before := jsonb_build_object('code', OLD.code, 'name', OLD.name, 'kit_id', OLD.kit_id,
                                   'status', OLD.status, 'notes', OLD.notes);
    IF v_before = v_after THEN
      RETURN NULL;
    END IF;
    v_type := CASE WHEN OLD.status <> NEW.status THEN 'status' ELSE 'update' END;
  END IF;
  INSERT INTO surgical_tray_history (tray_id, version, changed_by_user_id, change_type, before, after)
  VALUES (NEW.id, NEW.version, COALESCE(NEW.updated_by_user_id, NEW.created_by_user_id), v_type, v_before, v_after);
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_surgical_tray_history ON surgical_tray;
CREATE TRIGGER trg_surgical_tray_history AFTER INSERT OR UPDATE ON surgical_tray
  FOR EACH ROW EXECUTE FUNCTION surgical_tray_record_history();

-- Relación con sesiones (opcional; referencias históricas: RESTRICT, una charola usada no se borra).
ALTER TABLE work_session ADD COLUMN IF NOT EXISTS tray_id UUID;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_work_session_tray') THEN
    ALTER TABLE work_session ADD CONSTRAINT fk_work_session_tray
      FOREIGN KEY (tray_id) REFERENCES surgical_tray (id) ON DELETE RESTRICT;
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_work_session_tray ON work_session (tray_id);

-- Sincronización (MS-07): si la migración 014 está aplicada, las charolas también se registran en sync_change.
DO $$
BEGIN
  IF to_regprocedure('sync_capture_change()') IS NOT NULL THEN
    DROP TRIGGER IF EXISTS trg_sync_surgical_tray ON surgical_tray;
    CREATE TRIGGER trg_sync_surgical_tray AFTER INSERT OR UPDATE OR DELETE ON surgical_tray
      FOR EACH ROW EXECUTE FUNCTION sync_capture_change('surgical_tray');
  END IF;
END $$;
