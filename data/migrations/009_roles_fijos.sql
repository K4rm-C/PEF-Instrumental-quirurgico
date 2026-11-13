-- PEF 009 (ADMIN-01.1, RF-AD-06): the four fixed product roles exist in every institution.
-- station_operator, spd_supervisor, it_admin and or_charge (floor supervisor, App - Part B).
--
-- Idempotent and data-preserving: only inserts the codes an institution is missing
-- (ON CONFLICT on uk_role_institution_code). Existing roles, their descriptions and every
-- user_role assignment are left untouched; no role is deleted or renamed. Roles with other
-- codes, if any, are not changed here (the portal no longer lets anyone create them).
-- Safe on a new database (runs after the seeds) and on an existing one, any number of times.
BEGIN;

INSERT INTO role (code, description, institution_id)
SELECT product.code, product.description, i.id
FROM institution i
CROSS JOIN (VALUES
  ('station_operator', 'Station operator. Runs assigned counting sessions.'),
  ('spd_supervisor', 'SPD supervisor. Schedules sessions, privacy notice, and closes cases.'),
  ('it_admin', 'Technical administrator. Manages catalogs, users, and configuration.'),
  ('or_charge', 'Floor supervisor. Monitors operating rooms and comments on OR holds; does not close SPD cycles or edit catalogs.')
) AS product (code, description)
ON CONFLICT (institution_id, code) DO NOTHING;

COMMIT;
