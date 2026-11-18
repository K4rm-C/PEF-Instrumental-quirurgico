-- =============================================================================
-- PEF · 004_media_asset_session.sql · Evidence E0 (PEF-DS05 v0.2.0)
--
-- Links a clinical evidence asset to its counting session:
--   media_asset.session_id  UUID NULL  → work_session(id)  ON DELETE RESTRICT
--
-- * NULL is valid: DS03 / MLOps assets (yolo_model weights, family_example photos) have no
--   session and stay outside the clinical Evidence API v1.
-- * Tenant of a clinical asset = session → operation → institution (no institution_id column;
--   object_key is never an authorization source).
-- * RESTRICT (not CASCADE / SET NULL): clinical evidence must never disappear or lose its
--   tenant silently because a work_session row is deleted; evidence leaves only through the
--   DS05/DS07 block/purge cycle.
-- * Existing rows are not modified (they keep session_id = NULL).
--
-- Idempotent: safe to run on a database that already has the column/constraint/index.
--
-- Apply (existing local database, from the repository root):
--   docker exec -i pef_postgres psql -v ON_ERROR_STOP=1 -U MedAdmin -d InstruMed < data/migrations/004_media_asset_session.sql
-- Order for a fresh database: 001_init.sql → 002_seed_catalogs.sql → 003_seed_demo.sql → 004 (this file).
-- =============================================================================

BEGIN;

ALTER TABLE media_asset
  ADD COLUMN IF NOT EXISTS session_id UUID NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fk_media_asset_session' AND conrelid = 'media_asset'::regclass
  ) THEN
    ALTER TABLE media_asset
      ADD CONSTRAINT fk_media_asset_session
      FOREIGN KEY (session_id) REFERENCES work_session (id) ON DELETE RESTRICT;
  END IF;
END
$$;

-- GET /api/v1/sessions/{id}/evidence: WHERE session_id = ? ORDER BY created_at, id.
CREATE INDEX IF NOT EXISTS idx_media_asset_session_created
  ON media_asset (session_id, created_at, id)
  WHERE session_id IS NOT NULL;

COMMENT ON COLUMN media_asset.session_id IS
  'Clinical evidence: owning work_session (tenant = session -> operation -> institution). NULL for DS03/MLOps assets.';

COMMIT;
