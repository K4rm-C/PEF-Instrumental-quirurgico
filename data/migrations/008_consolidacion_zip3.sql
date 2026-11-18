-- Consolidation of schema additions originating in ZIP3. Safe for initialized databases.
-- Preserves V1 migrations 004-007 (evidence, count references, notifications and monitoring).
BEGIN;
CREATE TABLE IF NOT EXISTS user_operating_room (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES "user" (id) ON DELETE CASCADE,
  room_id UUID NOT NULL REFERENCES operating_room (id) ON DELETE CASCADE,
  assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uk_user_operating_room_user_room UNIQUE (user_id, room_id)
);
ALTER TABLE yolo_model ADD COLUMN IF NOT EXISTS architecture VARCHAR(64);
ALTER TABLE yolo_model ADD COLUMN IF NOT EXISTS model_size VARCHAR(8);
ALTER TABLE work_session ADD COLUMN IF NOT EXISTS yolo_model_id UUID;
ALTER TABLE discrepancy ADD COLUMN IF NOT EXISTS suggested_resolution TEXT;
ALTER TABLE discrepancy ADD COLUMN IF NOT EXISTS or_released_at TIMESTAMPTZ;
ALTER TABLE discrepancy ADD COLUMN IF NOT EXISTS or_released_by_user_id UUID;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_work_session_yolo_model') THEN
    ALTER TABLE work_session ADD CONSTRAINT fk_work_session_yolo_model
      FOREIGN KEY (yolo_model_id) REFERENCES yolo_model(id) ON DELETE SET NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_discrepancy_or_released_by') THEN
    ALTER TABLE discrepancy ADD CONSTRAINT fk_discrepancy_or_released_by
      FOREIGN KEY (or_released_by_user_id) REFERENCES "user"(id) ON DELETE SET NULL;
  END IF;
END $$;
ALTER TABLE media_asset DROP CONSTRAINT IF EXISTS chk_media_asset_kind;
-- MS-11: NOT VALID — re-applying this (idempotent) migration must not re-check rows written by NEWER types of
-- later migrations (e.g. 017 notification types); the constraint still applies to every new or updated row and
-- the last migration of the sequence defines the final allowed set.
ALTER TABLE media_asset ADD CONSTRAINT chk_media_asset_kind CHECK
  (kind IN ('jpeg','gif','mp4','mkv','webm','weights','other')) NOT VALID;
INSERT INTO cat_session_status (code,name) VALUES ('or_hold','OR hold')
 ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name;
INSERT INTO cat_event_type (code,name) VALUES
 ('or_hold_opened','OR hold opened (human shortfall)'),
 ('or_hold_comment','OR hold comment'),
 ('or_hold_released','OR hold released to SPD')
 ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name;
COMMIT;
