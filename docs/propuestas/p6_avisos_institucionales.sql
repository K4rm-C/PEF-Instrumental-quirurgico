-- =============================================================================
-- PROPUESTA (no aplicada, no montada en ningún Compose, no está en data/migrations): P6, opción (b).
-- Avisos de privacidad por institución con los avisos existentes preservados como GLOBALES.
-- Requiere aprobación funcional (GUIA_PRIVACIDAD_P6.md §4-§5) antes de copiarla a data/migrations.
-- Idempotente; no modifica ni borra filas existentes.
-- =============================================================================

ALTER TABLE privacy_notice_version ADD COLUMN IF NOT EXISTS institution_id UUID;   -- NULL = aviso global (histórico)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_privacy_notice_version_institution') THEN
    ALTER TABLE privacy_notice_version ADD CONSTRAINT fk_privacy_notice_version_institution
      FOREIGN KEY (institution_id) REFERENCES institution (id) ON DELETE RESTRICT;
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_privacy_notice_version_institution ON privacy_notice_version (institution_id);

-- Un acuerdo de sesión solo puede usar un aviso global o de la institución de la sesión (también fuera de la API).
CREATE OR REPLACE FUNCTION session_agreement_notice_scope() RETURNS TRIGGER AS $$
BEGIN
  IF EXISTS (
      SELECT 1 FROM privacy_notice_version n, work_session w JOIN operation o ON o.id = w.operation_id
      WHERE n.id = NEW.privacy_notice_version_id AND w.id = NEW.session_id
        AND n.institution_id IS NOT NULL AND n.institution_id <> o.institution_id) THEN
    RAISE EXCEPTION 'privacy notice of another institution' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_session_agreement_notice_scope ON session_processing_agreement;
CREATE TRIGGER trg_session_agreement_notice_scope BEFORE INSERT OR UPDATE ON session_processing_agreement
  FOR EACH ROW EXECUTE FUNCTION session_agreement_notice_scope();
