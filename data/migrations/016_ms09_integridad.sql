-- =============================================================================
-- 016 — MS-09: integridad de charolas en sesiones (idempotente; no modifica datos)
--
-- Una sesión solo puede referirse a una charola de su misma institución (la de su operación), también para
-- escrituras fuera de la API. La validación de la API sigue siendo la principal (estado activo, kit igual);
-- este trigger es la defensa en la base de datos contra asociaciones entre instituciones.
-- =============================================================================

CREATE OR REPLACE FUNCTION work_session_tray_same_institution() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.tray_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.tray_id IS DISTINCT FROM OLD.tray_id) THEN
    IF NOT EXISTS (
        SELECT 1 FROM surgical_tray t JOIN operation o ON o.id = NEW.operation_id
        WHERE t.id = NEW.tray_id AND t.institution_id = o.institution_id) THEN
      RAISE EXCEPTION 'work_session tray must belong to the institution of the session operation' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
  IF to_regclass('surgical_tray') IS NOT NULL THEN
    DROP TRIGGER IF EXISTS trg_work_session_tray_same_institution ON work_session;
    CREATE TRIGGER trg_work_session_tray_same_institution BEFORE INSERT OR UPDATE OF tray_id ON work_session
      FOR EACH ROW EXECUTE FUNCTION work_session_tray_same_institution();
  END IF;
END $$;
