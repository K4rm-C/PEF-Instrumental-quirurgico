-- =============================================================================
-- PEF · 004_seed_demo_locust.sql
-- Contenido FUSIONADO en 003_seed_demo.sql (sección 14).
-- Este archivo se conserva como re-aplicación IDEMPOTENTE (ON CONFLICT) para:
--   - volúmenes Postgres antiguos que solo tenían 003 pre-fusión
--   - scripts/seed_demo_volume.py (lo ejecuta antes de generar volumen)
-- DEV/DEMO only. REQUISITOS: 001–003 + migraciones aplicadas.
-- =============================================================================

BEGIN;

INSERT INTO operating_room (id, code, name, active, institution_id) VALUES
  ('12121212-0000-4000-8000-000000000002', 'OR-02', 'Operating Room 2', TRUE,
   '11111111-1111-1111-1111-111111111111')
ON CONFLICT (id) DO NOTHING;

INSERT INTO capture_station (id, name, roi, active, room_id) VALUES
  ('13131313-0000-4000-8000-000000000002', 'Capture Station CS-02',
   '{"shape": "rect", "x": 240, "y": 120, "w": 1180, "h": 760, "frame_w": 1920, "frame_h": 1080}'::jsonb,
   TRUE, '12121212-0000-4000-8000-000000000002')
ON CONFLICT (id) DO NOTHING;

INSERT INTO "user" (id, name, email, password_hash, active, ui_preferences, institution_id) VALUES
  ('22222222-2222-2222-2222-222222222231', 'Operator Ana Ruiz', 'ana.ruiz@instrumed.com',
   'pbkdf2:sha256:600000$pBt90bMOFJshdYWs$4a2d7fe6185d1787ebfeaf2202863fbed1a0a8cfa19848a907327fdb9baf25d4', TRUE,
   '{"theme": "light", "locale": "es-MX"}'::jsonb, '11111111-1111-1111-1111-111111111111'),
  ('22222222-2222-2222-2222-222222222232', 'Operator Luis Pena', 'luis.pena@instrumed.com',
   'pbkdf2:sha256:600000$ruYQcXa3zGai8fon$5440ce1335a005785bdcebe25864be96cc67f36c1a273c80f644ec0068b7acd6', TRUE,
   '{"theme": "light", "locale": "en"}'::jsonb, '11111111-1111-1111-1111-111111111111'),
  ('22222222-2222-2222-2222-222222222233', 'SPD Carla Soto', 'carla.soto@instrumed.com',
   'pbkdf2:sha256:600000$OIkAMI7h31rAH0wL$d3e48c1db70235e51f3fe293bbf67b14b08ff00deb458c1a7505f7244311d562', TRUE,
   '{"theme": "dark", "locale": "es-MX"}'::jsonb, '11111111-1111-1111-1111-111111111111'),
  ('22222222-2222-2222-2222-222222222234', 'IT Admin Extra', 'ti.extra@instrumed.com',
   'pbkdf2:sha256:600000$cgRkTgIJBmVmDGXI$0f5298444ecf2ea9c9f8fb20f403061d11968b2fe71e7ca39445937f205ef000', TRUE,
   '{"theme": "system", "locale": "en"}'::jsonb, '11111111-1111-1111-1111-111111111111'),
  ('22222222-2222-2222-2222-222222222235', 'Floor Nora Diaz', 'nora.diaz@instrumed.com',
   'pbkdf2:sha256:600000$M0mNEmXKC6clH5og$bcd831c54266d09d194cd97bd9e943832cfeffec524c148de279a6d8cdbdd226', TRUE,
   '{"theme": "light", "locale": "es-MX"}'::jsonb, '11111111-1111-1111-1111-111111111111'),
  ('22222222-2222-2222-2222-222222222236', 'Dual Admin SPD', 'dual.admin.spd@instrumed.com',
   'pbkdf2:sha256:600000$P7YSsITcxlOhTEOm$2f30c96acf5bebe8f92a536ff83708abb25f26465fc10146fcc6dbc98a7b4418', TRUE,
   '{"theme": "system", "locale": "en"}'::jsonb, '11111111-1111-1111-1111-111111111111')
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name;

INSERT INTO user_role (user_id, role_id) VALUES
  ('22222222-2222-2222-2222-222222222231', '14141414-0000-4000-8000-000000000001'),
  ('22222222-2222-2222-2222-222222222232', '14141414-0000-4000-8000-000000000001'),
  ('22222222-2222-2222-2222-222222222233', '14141414-0000-4000-8000-000000000002'),
  ('22222222-2222-2222-2222-222222222234', '14141414-0000-4000-8000-000000000003'),
  ('22222222-2222-2222-2222-222222222235', '14141414-0000-4000-8000-000000000004'),
  ('22222222-2222-2222-2222-222222222236', '14141414-0000-4000-8000-000000000003'),
  ('22222222-2222-2222-2222-222222222236', '14141414-0000-4000-8000-000000000002'),
  ('22222222-2222-2222-2222-222222222221', '14141414-0000-4000-8000-000000000004')
ON CONFLICT DO NOTHING;

INSERT INTO user_operating_room (id, user_id, room_id) VALUES
  ('15151515-0000-4000-8000-000000000002',
   '22222222-2222-2222-2222-222222222235',
   '12121212-0000-4000-8000-000000000001'),
  ('15151515-0000-4000-8000-000000000003',
   '22222222-2222-2222-2222-222222222221',
   '12121212-0000-4000-8000-000000000001'),
  ('15151515-0000-4000-8000-000000000004',
   '22222222-2222-2222-2222-222222222226',
   '12121212-0000-4000-8000-000000000002')
ON CONFLICT (id) DO NOTHING;

INSERT INTO physician (id, name, active, institution_id) VALUES
  ('32323232-0000-4000-8000-000000000006', 'Dr. Sofia Navarro', TRUE,
   '11111111-1111-1111-1111-111111111111'),
  ('32323232-0000-4000-8000-000000000007', 'Dr. Marco Beltran', TRUE,
   '11111111-1111-1111-1111-111111111111'),
  ('32323232-0000-4000-8000-000000000008', 'Dr. Elena Rios', TRUE,
   '11111111-1111-1111-1111-111111111111')
ON CONFLICT (id) DO NOTHING;

INSERT INTO physician_specialty (physician_id, specialty_id)
SELECT v.physician_id::uuid, s.id
FROM (VALUES
  ('32323232-0000-4000-8000-000000000006', 'general_surgery'),
  ('32323232-0000-4000-8000-000000000007', 'general_surgery'),
  ('32323232-0000-4000-8000-000000000008', 'general_surgery')
) AS v(physician_id, specialty_code)
JOIN cat_specialty s ON s.code = v.specialty_code
ON CONFLICT DO NOTHING;

INSERT INTO instrument (id, internal_code, active, family_id, cycle_status_id, institution_id)
SELECT v.id::uuid, v.internal_code, TRUE, f.id, cs.id, '11111111-1111-1111-1111-111111111111'
FROM (VALUES
  ('42424242-0000-4000-8000-000000000101', 'LOC-ADSON-WT-L1', 'Adson without teeth', 'available'),
  ('42424242-0000-4000-8000-000000000102', 'LOC-ADSON-WT-L2', 'Adson without teeth', 'sterilization'),
  ('42424242-0000-4000-8000-000000000103', 'LOC-MAYO-S-L1', 'Straight Mayo Scissor', 'maintenance'),
  ('42424242-0000-4000-8000-000000000104', 'LOC-PROBE-L1', 'Surgical probe', 'available'),
  ('42424242-0000-4000-8000-000000000105', 'LOC-PROBE-L2', 'Surgical probe', 'retired'),
  ('42424242-0000-4000-8000-000000000106', 'LOC-KELLY-L1', 'Straight Kelly Forceps', 'lost')
) AS v(id, internal_code, family_name, status_code)
JOIN instrument_family f ON f.name = v.family_name
JOIN cat_instrument_cycle_status cs ON cs.code = v.status_code
ON CONFLICT (id) DO NOTHING;

INSERT INTO operation (id, scheduled_at, started_at, ended_at, status_id, procedure_type_id, room_id, institution_id)
SELECT '61616161-0000-4000-8000-000000000011',
       '2026-10-10T08:00:00Z', '2026-10-10T08:20:00Z', '2026-10-10T09:40:00Z',
       st.id, pt.id, '12121212-0000-4000-8000-000000000002',
       '11111111-1111-1111-1111-111111111111'
FROM cat_operation_status st, cat_procedure_type pt
WHERE st.code = 'closed' AND pt.code = 'lap_chole'
ON CONFLICT (id) DO NOTHING;

INSERT INTO operation_physician (operation_id, physician_id, surgical_role_id)
SELECT '61616161-0000-4000-8000-000000000011', '32323232-0000-4000-8000-000000000006', r.id
FROM cat_surgical_role r WHERE r.code = 'surgeon'
ON CONFLICT DO NOTHING;

INSERT INTO work_session (
  id, started_at, ended_at, status_id, user_id, closed_by_user_id,
  operation_id, station_id, kit_id, current_phase_id, phase_changed_at,
  capture_mode, atypical_session, extended_retention, retention_until)
SELECT 'c100000a-0000-4000-8000-000000000001',
       '2026-10-10T08:25:00Z', '2026-10-10T09:35:00Z',
       ss.id,
       '22222222-2222-2222-2222-222222222231',
       '22222222-2222-2222-2222-222222222233',
       '61616161-0000-4000-8000-000000000011',
       '13131313-0000-4000-8000-000000000002',
       '51515151-0000-4000-8000-000000000001',
       ph.id, '2026-10-10T09:30:00Z',
       'manual_no_privacy', FALSE, FALSE, NULL
FROM cat_session_status ss, cat_operation_phase ph
WHERE ss.code = 'closed' AND ph.code = 'final_count'
ON CONFLICT (id) DO NOTHING;

INSERT INTO operation (id, scheduled_at, started_at, ended_at, status_id, procedure_type_id, room_id, institution_id)
SELECT '61616161-0000-4000-8000-000000000012',
       (date_trunc('day', now() AT TIME ZONE 'UTC') + interval '10 hours'),
       (now() AT TIME ZONE 'UTC' - interval '20 minutes'), NULL,
       st.id, pt.id, '12121212-0000-4000-8000-000000000002',
       '11111111-1111-1111-1111-111111111111'
FROM cat_operation_status st, cat_procedure_type pt
WHERE st.code = 'in_progress' AND pt.code = 'lap_chole'
ON CONFLICT (id) DO NOTHING;

INSERT INTO operation_physician (operation_id, physician_id, surgical_role_id)
SELECT '61616161-0000-4000-8000-000000000012', '32323232-0000-4000-8000-000000000007', r.id
FROM cat_surgical_role r WHERE r.code = 'surgeon'
ON CONFLICT DO NOTHING;

INSERT INTO work_session (
  id, started_at, ended_at, status_id, user_id, closed_by_user_id,
  operation_id, station_id, kit_id, current_phase_id, phase_changed_at,
  capture_mode, atypical_session, extended_retention, retention_until)
SELECT 'c100000b-0000-4000-8000-000000000001',
       (now() AT TIME ZONE 'UTC' - interval '15 minutes'), NULL,
       ss.id,
       '22222222-2222-2222-2222-222222222232',
       NULL,
       '61616161-0000-4000-8000-000000000012',
       '13131313-0000-4000-8000-000000000002',
       '51515151-0000-4000-8000-000000000001',
       ph.id, (now() AT TIME ZONE 'UTC' - interval '10 minutes'),
       'vision', FALSE, FALSE, NULL
FROM cat_session_status ss, cat_operation_phase ph
WHERE ss.code = 'in_progress' AND ph.code = 'start'
ON CONFLICT (id) DO NOTHING;

INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source)
SELECT 'c100000b-0000-4000-8000-000000000001', ki.family_id, ki.quantity, 'kit_snapshot'
FROM kit_item ki
WHERE ki.kit_id = '51515151-0000-4000-8000-000000000001'
ON CONFLICT DO NOTHING;

INSERT INTO procedure_kit (procedure_type_id, kit_id, is_default, active)
SELECT pt.id, '51515151-0000-4000-8000-000000000001', TRUE, TRUE
FROM cat_procedure_type pt
WHERE pt.code = 'demo'
ON CONFLICT DO NOTHING;

COMMIT;
