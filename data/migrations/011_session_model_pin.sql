-- PEF 011 (MS-02, P1): the YOLO model pinned to a work session never changes.
--
-- work_session.yolo_model_id (migration 008) is set once, when a vision session starts (or at its first bind if
-- no model was active then). From then on it is immutable: an Administration activation of another model never
-- touches started sessions. Deleting a yolo_model that sessions reference is therefore refused too (the FK's
-- ON DELETE SET NULL would change the pin). Setting it the first time (NULL -> value) stays allowed.
-- Idempotent and data-preserving: no row is read or modified.

CREATE OR REPLACE FUNCTION pef_work_session_model_pin_immutable() RETURNS trigger AS $$
BEGIN
  IF OLD.yolo_model_id IS NOT NULL AND NEW.yolo_model_id IS DISTINCT FROM OLD.yolo_model_id THEN
    RAISE EXCEPTION 'work_session % already uses yolo_model %; the pinned model cannot change',
      OLD.id, OLD.yolo_model_id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_work_session_model_pin_immutable ON work_session;
CREATE TRIGGER trg_work_session_model_pin_immutable
  BEFORE UPDATE OF yolo_model_id ON work_session
  FOR EACH ROW EXECUTE FUNCTION pef_work_session_model_pin_immutable();

CREATE INDEX IF NOT EXISTS ix_work_session_yolo_model ON work_session (yolo_model_id) WHERE yolo_model_id IS NOT NULL;
