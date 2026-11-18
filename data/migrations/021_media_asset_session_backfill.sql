-- 021: backfill media_asset.session_id for EvidenceWorker clips published without it.
-- Worker object_key = {session_id}/{yyyy}/{mm}/{filename}. Idempotent.

BEGIN;

UPDATE media_asset AS ma
SET session_id = ws.id,
    updated_at = now()
FROM work_session AS ws
WHERE ma.session_id IS NULL
  AND ma.object_key ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/'
  AND ws.id = CAST(split_part(ma.object_key, '/', 1) AS uuid);

COMMIT;
