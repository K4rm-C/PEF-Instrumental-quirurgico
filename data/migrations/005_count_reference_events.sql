-- =============================================================================
-- PEF · 005_count_reference_events.sql · Count reference (PEF-DS04 v0.3.0)
--
-- Catalog value only (no schema change): count_event type for instruments physically added
-- to the tray during an in-progress session and confirmed by the SPD Supervisor. The vision
-- count reference of a session is its initial YOLO count plus these confirmed additions.
--
-- Idempotent: a new PostgreSQL volume already gets the row from 002_seed_catalogs.sql;
-- an existing database applies this file once by hand:
--   Get-Content data/migrations/005_count_reference_events.sql |
--     docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U <POSTGRES_USER> -d InstruMed
-- =============================================================================

INSERT INTO cat_event_type (code, name) VALUES
  ('instruments_added', 'Instruments added to the tray (confirmed)')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;
