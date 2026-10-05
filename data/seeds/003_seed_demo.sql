-- =============================================================================
-- PEF · 003_seed_demo.sql
-- Datos de demostracion.
--
-- AMBITO: SOLO desarrollo y demostracion. NO ejecutar en produccion.
--
-- Todos los datos son inventados. No se emplea informacion real de pacientes,
-- medicos ni personal en ningun ambiente del proyecto. Esta es una medida de
-- minimizacion coherente con el marco de privacidad del apartado 5.1.10 del
-- reporte y con el analisis de restricciones legales del apartado 1.10.
--
-- REQUISITOS: 001_init.sql y 002_seed_catalogs.sql ejecutados previamente.
--
-- CONTRASENAS: hashes werkzeug pbkdf2 sha256 (mismo esquema que BackendAuthService).
-- operator@instrumed.com / DemopwdOP78!
-- operador2@instrumed.com / DemopwdOP278!
-- supervisor@instrumed.com / DemopwdSPD78!
-- supervisor2@instrumed.com / DemopwdSPD278!
-- admin@instrumed.com / DemopwdADM78!
--
-- ESCENARIO QUE CONSTRUYE:
--   1. Una institucion con un quirofano y una estacion de captura con ROI.
--   2. Tres usuarios, uno por cada perfil del apartado 2 del reporte.
--   3. Cuatro familias de instrumental con sus textos pedagogicos.
--   4. Piezas individuales por familia, en cantidad suficiente para que la
--      reserva de un kit tenga exito y para poder provocar deliberadamente
--      un caso de inventario insuficiente.
--   5. Un kit versionado ligado a un tipo de procedimiento.
--   6. Una secuencia de fases para ese procedimiento.
--   7. Una operacion programada con paciente y medico asignados.
--   8. Una sesion de conteo cerrada con eventos, una discrepancia y su
--      correccion humana justificada.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Institucion, quirofano y estacion de captura
-- -----------------------------------------------------------------------------
INSERT INTO institution (id, name, active) VALUES
  ('11111111-1111-1111-1111-111111111111', 'Hospital Demo PEF', TRUE);

INSERT INTO operating_room (id, code, name, active, institution_id) VALUES
  ('12121212-0000-4000-8000-000000000001', 'OR-01', 'Quirofano 1', TRUE,
   '11111111-1111-1111-1111-111111111111');

-- La region de interes delimita el area de charola dentro del encuadre.
INSERT INTO capture_station (id, name, roi, active, room_id) VALUES
  ('13131313-0000-4000-8000-000000000001', 'Capture Station CS-01',
   '{"shape": "rect", "x": 240, "y": 120, "w": 1180, "h": 760, "frame_w": 1920, "frame_h": 1080}'::jsonb,
   TRUE, '12121212-0000-4000-8000-000000000001');

-- -----------------------------------------------------------------------------
-- 2. Roles y usuarios (los tres perfiles del apartado 2 del reporte)
-- -----------------------------------------------------------------------------
-- Codigos de rol = RF (station_operator / spd_supervisor). it_admin = catalogo tecnico.
INSERT INTO role (id, code, description, institution_id) VALUES
  ('14141414-0000-4000-8000-000000000001', 'station_operator',
   'Operador de estacion. Ejecuta sesiones de conteo asignadas.',
   '11111111-1111-1111-1111-111111111111'),
  ('14141414-0000-4000-8000-000000000002', 'spd_supervisor',
   'Supervisor SPD. Programa sesiones, aviso de privacidad y cierra casos.',
   '11111111-1111-1111-1111-111111111111'),
  ('14141414-0000-4000-8000-000000000003', 'it_admin',
   'Administrador Tecnico. Gestiona catalogos, usuarios y configuracion.',
   '11111111-1111-1111-1111-111111111111');

-- ui_preferences: clave DS01 `locale` (en | es-MX) + theme. Babel/traduccion real = fase i18n posterior.
INSERT INTO "user" (id, name, email, password_hash, active, ui_preferences, institution_id) VALUES
  ('22222222-2222-2222-2222-222222222221', 'Operador Demo', 'operator@instrumed.com',
   'pbkdf2:sha256:600000$q1YkN7fvtlFnwfZc$04dfe82983804ead8a83f535479601217903e355e16d32f20d0152ae970cba79', TRUE,
   '{"theme": "light", "locale": "en"}'::jsonb, '11111111-1111-1111-1111-111111111111'),
  ('22222222-2222-2222-2222-222222222224', 'Operador Demo 2', 'operador2@instrumed.com',
   'pbkdf2:sha256:600000$Upoc6Ut0Tb6S6XID$2f48c677cb92cdc0484a6ff624246a2a9defb926bf414a414f07d5c4dc30f337', TRUE,
   '{"theme": "light", "locale": "en"}'::jsonb, '11111111-1111-1111-1111-111111111111'),
  ('22222222-2222-2222-2222-222222222222', 'Supervisor Demo', 'supervisor@instrumed.com',
   'pbkdf2:sha256:600000$4nOgHclHpgqor4sA$adb64c6ed577594e684d746190dae5fab45eb239dae93f73a1dd42b9e00f234e', TRUE,
   '{"theme": "dark", "locale": "en"}'::jsonb, '11111111-1111-1111-1111-111111111111'),
  ('22222222-2222-2222-2222-222222222225', 'Supervisor Demo 2', 'supervisor2@instrumed.com',
   'pbkdf2:sha256:600000$gkf3FVXr0T15vkTq$5f7bac349638c0c81e6b95d7ce07a1074fd7be6fba3e411b2463da937066aeed', TRUE,
   '{"theme": "dark", "locale": "en"}'::jsonb, '11111111-1111-1111-1111-111111111111'),
  ('22222222-2222-2222-2222-222222222223', 'Admin Demo', 'admin@instrumed.com',
   'pbkdf2:sha256:600000$7c8qfwZeUHfYRb6N$7ffb6e05addb4ed80157d1e532c25da5d2412ba16937f15eaf45eb01fb09f32e', TRUE,
   '{"theme": "system", "locale": "en"}'::jsonb, '11111111-1111-1111-1111-111111111111');

INSERT INTO user_role (user_id, role_id) VALUES
  ('22222222-2222-2222-2222-222222222221', '14141414-0000-4000-8000-000000000001'),
  ('22222222-2222-2222-2222-222222222224', '14141414-0000-4000-8000-000000000001'),
  ('22222222-2222-2222-2222-222222222222', '14141414-0000-4000-8000-000000000002'),
  ('22222222-2222-2222-2222-222222222225', '14141414-0000-4000-8000-000000000002'),
  ('22222222-2222-2222-2222-222222222223', '14141414-0000-4000-8000-000000000003');

-- -----------------------------------------------------------------------------
-- 3. Paciente y medico (datos minimos, sin historia clinica)
-- -----------------------------------------------------------------------------
INSERT INTO patient (id, display_name, birth_date, active, gender_id, institution_id)
SELECT '31313131-0000-4000-8000-000000000001', 'Paciente Demo 001', DATE '1985-04-17', TRUE,
       g.id, '11111111-1111-1111-1111-111111111111'
FROM cat_gender g WHERE g.code = 'female';

INSERT INTO physician (id, name, active, institution_id) VALUES
  ('32323232-0000-4000-8000-000000000001', 'Dr. Hector Villarreal', TRUE,
   '11111111-1111-1111-1111-111111111111'),
  ('32323232-0000-4000-8000-000000000002', 'Dra. Laura Mendoza', TRUE,
   '11111111-1111-1111-1111-111111111111'),
  ('32323232-0000-4000-8000-000000000003', 'Dr. Andres Castillo', TRUE,
   '11111111-1111-1111-1111-111111111111'),
  ('32323232-0000-4000-8000-000000000004', 'Dra. Sofia Ramirez', TRUE,
   '11111111-1111-1111-1111-111111111111'),
  ('32323232-0000-4000-8000-000000000005', 'Dr. Miguel Angel Torres', TRUE,
   '11111111-1111-1111-1111-111111111111');

INSERT INTO physician_specialty (physician_id, specialty_id)
SELECT v.physician_id::uuid, s.id
FROM (VALUES
  ('32323232-0000-4000-8000-000000000001', 'general_surgery'),
  ('32323232-0000-4000-8000-000000000002', 'general_surgery'),
  ('32323232-0000-4000-8000-000000000003', 'general_surgery'),
  ('32323232-0000-4000-8000-000000000004', 'general_surgery'),
  ('32323232-0000-4000-8000-000000000005', 'general_surgery')
) AS v(physician_id, specialty_code)
JOIN cat_specialty s ON s.code = v.specialty_code;

-- Identificador externo simulado: asi llegaria el paciente desde un HIS.
INSERT INTO resource_identifier (resource_type, resource_id, system, value, use_code, active)
VALUES ('patient', '31313131-0000-4000-8000-000000000001',
        'https://his.demo.local/mrn', 'MRN-0098231', 'official', TRUE);

-- -----------------------------------------------------------------------------
-- 4. Familias de instrumental (Video Demo Kit 1 — English display names)
-- -----------------------------------------------------------------------------
INSERT INTO instrument_family (id, code, name, category_id, identify_text, classify_text, function_text, active)
SELECT v.id::uuid, v.code, v.name, c.id, v.identify_text, v.classify_text, v.function_text, TRUE
FROM (VALUES
  -- Names aligned to PEF/Image Dataset/classes.txt (17 labels).
  ('41414141-0000-4000-8000-000000000001', 'POZZI',          'Pozzi forceps',              'grasping',
   'Forceps with interlocking jaws used for tissue or sponge handling.',
   'Grasping instrument; longer jaws than Adson, typically ring-handled.',
   'Hold tissue or sponges during operative exposure.'),
  ('41414141-0000-4000-8000-000000000002', 'SCALPEL3',       'Scalpel handle #3',          'cutting',
   'Flat handle with a distal slot for disposable blades (size #3).',
   'Cutting instrument; no scissors blades, only a blade mount.',
   'Mount a blade to make controlled skin or tissue incisions.'),
  ('41414141-0000-4000-8000-000000000003', 'MAYO_CURVED',    'Curved Mayo Scissor',        'cutting',
   'Heavy scissors with curved blades and blunt tips.',
   'Cutting instrument for denser tissue; curved blade profile.',
   'Cut fascia or tougher tissue along a curved path.'),
  ('41414141-0000-4000-8000-000000000004', 'MAYO_STRAIGHT',  'Straight Mayo Scissor',      'cutting',
   'Heavy scissors with straight blades and blunt tips.',
   'Cutting instrument for denser tissue; straight blade profile.',
   'Cut suture or tougher tissue in a straight line.'),
  ('41414141-0000-4000-8000-000000000005', 'KELLY_FORCEPS',  'Straight Kelly Forceps',     'hemostasis',
   'Hemostatic forceps with partial-jaw serrations (Kelly profile).',
   'Hemostasis instrument; distinct from Mayo scissors and Rochester-Pean.',
   'Clamp vessels or tissue for hemostasis.'),
  ('41414141-0000-4000-8000-000000000006', 'ALLIS',          'Allis forceps',              'grasping',
   'Ring forceps with multiple interlocking teeth at the tip.',
   'Grasping instrument for firmer tissue purchase.',
   'Hold or retract denser tissue edges.'),
  ('41414141-0000-4000-8000-000000000007', 'FOERSTER',       'Foerster forceps',           'grasping',
   'Long ring forceps with oval fenestrated jaws (sponge stick).',
   'Grasping instrument for sponges; fenestrated jaws.',
   'Hold sponges for blotting or packing.'),
  ('41414141-0000-4000-8000-000000000008', 'ROCHESTER',      'Rochester-Pean forceps',     'hemostasis',
   'Hemostatic forceps with transverse serrations extending to the tip.',
   'Hemostasis instrument; fuller serration than classic Kelly forceps.',
   'Clamp larger vessels or pedicles.'),
  ('41414141-0000-4000-8000-000000000009', 'OLSENHEG',       'Olsen-Hegar needle holder',  'suturing',
   'Needle holder with built-in suture-cutting scissors near the jaws.',
   'Suturing instrument; combined holder and cutter.',
   'Hold the needle and cut suture without changing instruments.'),
  ('41414141-0000-4000-8000-000000000010', 'MAYOHEG',        'Mayo-Hegar needle holder',   'suturing',
   'Needle holder with short, broad jaws and cross-hatched grip.',
   'Suturing instrument without integrated scissors.',
   'Hold the needle firmly while passing suture.'),
  ('41414141-0000-4000-8000-000000000011', 'FARABEUF',       'Farabeuf retractor',         'retraction',
   'Double-ended flat metal blade bent at both ends; no ratchet.',
   'Retraction instrument; used in pairs.',
   'Retract wound edges to expose the field.'),
  ('41414141-0000-4000-8000-000000000012', 'ADSON_PLAIN',    'Adson without teeth',        'grasping',
   'Fine tissue forceps with serrated tips and no teeth.',
   'Grasping instrument for delicate tissue; plain tips.',
   'Handle skin or delicate tissue with less trauma.'),
  ('41414141-0000-4000-8000-000000000013', 'ADSON_TEETH',    'Adson with teeth',           'grasping',
   'Fine tissue forceps with 1x2 teeth at the tip.',
   'Grasping instrument for delicate tissue; toothed tips.',
   'Hold skin or fascia with a more secure bite.'),
  ('41414141-0000-4000-8000-000000000014', 'MOSQUITO',       'Curve Mosquito Kocher',      'hemostasis',
   'Fine curved hemostatic forceps (mosquito / Kocher profile).',
   'Hemostasis instrument; smaller jaws than Kelly or Rochester.',
   'Clamp small vessels in delicate fields.'),
  ('41414141-0000-4000-8000-000000000015', 'SCALPEL4',       'Scalpel n4 S',               'cutting',
   'Scalpel handle sized for #4 blades (heavier than #3).',
   'Cutting instrument; blade mount for larger blades.',
   'Mount a #4 blade for larger incisions.'),
  ('41414141-0000-4000-8000-000000000016', 'CRILE_WOOD',     'Crile Wood',                 'suturing',
   'Needle holder with Crile-Wood jaw pattern.',
   'Suturing instrument; finer grip than Mayo-Hegar.',
   'Hold needles for finer suture work.'),
  ('41414141-0000-4000-8000-000000000017', 'PROBE',          'Surgical probe',             'dissection',
   'Slender probe used to explore tracts or cavities.',
   'Dissection / exploration instrument; no cutting edge.',
   'Explore tracts or guide blunt dissection.')
) AS v(id, code, name, category_code, identify_text, classify_text, function_text)
JOIN cat_instrument_category c ON c.code = v.category_code;

-- -----------------------------------------------------------------------------
-- 5. Piezas fisicas (>=5 available por familia; variedad de ciclo)
-- -----------------------------------------------------------------------------
INSERT INTO instrument (internal_code, family_id, cycle_status_id, institution_id, active)
SELECT
  f.code || '-A' || lpad(g::text, 3, '0'),
  f.id,
  s.id,
  '11111111-1111-1111-1111-111111111111',
  TRUE
FROM (VALUES
  ('POZZI', 5), ('SCALPEL3', 5), ('MAYO_CURVED', 5), ('MAYO_STRAIGHT', 5),
  ('KELLY_FORCEPS', 5), ('ALLIS', 5), ('FOERSTER', 5), ('ROCHESTER', 5),
  ('OLSENHEG', 5), ('MAYOHEG', 5), ('FARABEUF', 6), ('ADSON_PLAIN', 5), ('ADSON_TEETH', 5),
  ('MOSQUITO', 5), ('SCALPEL4', 5), ('CRILE_WOOD', 5), ('PROBE', 5)
) AS q(family_code, units)
JOIN instrument_family f ON f.code = q.family_code
CROSS JOIN LATERAL generate_series(1, q.units) AS g
CROSS JOIN cat_instrument_cycle_status s
WHERE s.code = 'available';

INSERT INTO instrument (internal_code, family_id, cycle_status_id, institution_id, active)
SELECT
  v.code || '-' || v.suffix,
  f.id,
  s.id,
  '11111111-1111-1111-1111-111111111111',
  v.active
FROM (VALUES
  ('POZZI', 'M001', 'maintenance', TRUE),
  ('SCALPEL3', 'S001', 'sterilization', TRUE),
  ('MAYO_CURVED', 'R001', 'retired', FALSE),
  ('KELLY_FORCEPS', 'M001', 'maintenance', TRUE),
  ('ALLIS', 'S001', 'sterilization', TRUE),
  ('ROCHESTER', 'L001', 'lost', FALSE),
  ('MAYOHEG', 'S001', 'sterilization', TRUE),
  ('FARABEUF', 'M001', 'maintenance', TRUE),
  ('ADSON_PLAIN', 'R001', 'retired', FALSE),
  ('ADSON_TEETH', 'M001', 'maintenance', TRUE),
  ('MOSQUITO', 'S001', 'sterilization', TRUE),
  ('SCALPEL4', 'M001', 'maintenance', TRUE),
  ('CRILE_WOOD', 'S001', 'sterilization', TRUE),
  ('PROBE', 'M001', 'maintenance', TRUE)
) AS v(code, suffix, status_code, active)
JOIN instrument_family f ON f.code = v.code
JOIN cat_instrument_cycle_status s ON s.code = v.status_code;

-- -----------------------------------------------------------------------------
-- 6. Kit Video Demo Kit 1
-- -----------------------------------------------------------------------------
INSERT INTO kit (id, name, version, active, institution_id) VALUES
  ('51515151-0000-4000-8000-000000000001', 'Video Demo Kit 1', 1, TRUE,
   '11111111-1111-1111-1111-111111111111');

INSERT INTO kit_item (kit_id, family_id, quantity)
SELECT '51515151-0000-4000-8000-000000000001', f.id, v.qty
FROM (VALUES
  ('POZZI', 1), ('SCALPEL3', 1), ('MAYO_CURVED', 1), ('MAYO_STRAIGHT', 1),
  ('KELLY_FORCEPS', 1), ('ALLIS', 1), ('FOERSTER', 1), ('ROCHESTER', 1),
  ('OLSENHEG', 1), ('MAYOHEG', 1), ('FARABEUF', 2), ('ADSON_PLAIN', 1), ('ADSON_TEETH', 1)
) AS v(family_code, qty)
JOIN instrument_family f ON f.code = v.family_code;

INSERT INTO procedure_kit (procedure_type_id, kit_id, technique_label, is_default, active)
SELECT pt.id, '51515151-0000-4000-8000-000000000001', 'Video demo', TRUE, TRUE
FROM cat_procedure_type pt WHERE pt.code = 'lap_chole';

-- Demo Kit 2: smaller mixed set for schedule / inventory QA
INSERT INTO kit (id, name, version, active, institution_id) VALUES
  ('51515151-0000-4000-8000-000000000002', 'Video Demo Kit 2', 1, TRUE,
   '11111111-1111-1111-1111-111111111111');

INSERT INTO kit_item (kit_id, family_id, quantity)
SELECT '51515151-0000-4000-8000-000000000002', f.id, v.qty
FROM (VALUES
  ('SCALPEL3', 2),
  ('MOSQUITO', 3),
  ('ADSON_TEETH', 2),
  ('CRILE_WOOD', 1),
  ('FARABEUF', 2),
  ('PROBE', 1)
) AS v(family_code, qty)
JOIN instrument_family f ON f.code = v.family_code;

INSERT INTO procedure_kit (procedure_type_id, kit_id, technique_label, is_default, active)
SELECT pt.id, '51515151-0000-4000-8000-000000000002', 'Compact demo', FALSE, TRUE
FROM cat_procedure_type pt WHERE pt.code = 'lap_chole';

-- -----------------------------------------------------------------------------
-- 7. Secuencia de fases del procedimiento (demo basico)
-- -----------------------------------------------------------------------------
INSERT INTO procedure_phase (procedure_type_id, phase_id, sort_order, is_count_required, active)
SELECT pt.id, ph.id, v.ord, v.req, TRUE
FROM cat_procedure_type pt
CROSS JOIN (VALUES
  ('start',        1, FALSE),
  ('demo_phase_1', 2, TRUE),
  ('demo_phase_2', 3, FALSE),
  ('demo_phase_3', 4, FALSE),
  ('final_count',  5, TRUE)
) AS v(phase_code, ord, req)
JOIN cat_operation_phase ph ON ph.code = v.phase_code
WHERE pt.code = 'lap_chole';

-- -----------------------------------------------------------------------------
-- 8. Operacion
-- -----------------------------------------------------------------------------
INSERT INTO operation (id, scheduled_at, started_at, ended_at, status_id, procedure_type_id, room_id, institution_id)
SELECT '61616161-0000-4000-8000-000000000001',
       '2026-09-05T15:30:00Z', '2026-09-05T16:00:00Z', '2026-09-05T18:10:00Z',
       st.id, pt.id, '12121212-0000-4000-8000-000000000001',
       '11111111-1111-1111-1111-111111111111'
FROM cat_operation_status st, cat_procedure_type pt
WHERE st.code = 'closed' AND pt.code = 'lap_chole';

INSERT INTO operation_patient (operation_id, patient_id) VALUES
  ('61616161-0000-4000-8000-000000000001', '31313131-0000-4000-8000-000000000001');

INSERT INTO operation_physician (operation_id, physician_id, surgical_role_id)
SELECT '61616161-0000-4000-8000-000000000001', '32323232-0000-4000-8000-000000000001', r.id
FROM cat_surgical_role r WHERE r.code = 'surgeon';

-- Reserva de piezas concretas para la operacion.
-- Nota: FARABEUF solo tiene 1 pieza y el kit pide 2. La reserva se hace de lo
-- disponible; el faltante es lo que despues genera la discrepancia.
INSERT INTO instrument_reservation (operation_id, instrument_id, reserved_at, released_at, active)
SELECT '61616161-0000-4000-8000-000000000001', i.id,
       '2026-09-05T15:45:00Z', '2026-09-05T18:15:00Z', FALSE
FROM instrument i
JOIN instrument_family f ON f.id = i.family_id
WHERE f.code IN (
  'POZZI', 'SCALPEL3', 'MAYO_CURVED', 'MAYO_STRAIGHT', 'KELLY_FORCEPS',
  'ALLIS', 'FOERSTER', 'ROCHESTER', 'OLSENHEG', 'MAYOHEG', 'FARABEUF',
  'ADSON_PLAIN', 'ADSON_TEETH'
);

-- -----------------------------------------------------------------------------
-- 9. Sesiones de conteo
--   A) cerrada (caso historico con vision + acuerdo SPD)
--   B) scheduled CON aviso (lista Begin → path vision stub)
--   C) scheduled SIN aviso (lista Begin → path manual_no_privacy)
-- -----------------------------------------------------------------------------
INSERT INTO work_session (
  id, started_at, ended_at, status_id, user_id, closed_by_user_id,
  operation_id, station_id, kit_id, current_phase_id, phase_changed_at,
  capture_mode, atypical_session, extended_retention, retention_until)
SELECT 'c1000001-0000-4000-8000-000000000001',
       '2026-09-05T16:02:11Z', '2026-09-05T18:12:40Z',
       ss.id,
       '22222222-2222-2222-2222-222222222221',
       '22222222-2222-2222-2222-222222222222',
       '61616161-0000-4000-8000-000000000001',
       '13131313-0000-4000-8000-000000000001',
       '51515151-0000-4000-8000-000000000001',
       ph.id, '2026-09-05T18:05:00Z',
       'vision', FALSE, FALSE, '2026-12-04T00:00:00Z'
FROM cat_session_status ss, cat_operation_phase ph
WHERE ss.code = 'closed' AND ph.code = 'final_count';

-- Operaciones programadas para sesiones B y C (mismo kit/OR/estacion demo).
INSERT INTO operation (id, scheduled_at, started_at, ended_at, status_id, procedure_type_id, room_id, institution_id)
SELECT '61616161-0000-4000-8000-000000000002',
       (date_trunc('day', now() AT TIME ZONE 'UTC') + interval '15 hours'), NULL, NULL,
       st.id, pt.id, '12121212-0000-4000-8000-000000000001',
       '11111111-1111-1111-1111-111111111111'
FROM cat_operation_status st, cat_procedure_type pt
WHERE st.code = 'scheduled' AND pt.code = 'lap_chole';

INSERT INTO operation (id, scheduled_at, started_at, ended_at, status_id, procedure_type_id, room_id, institution_id)
SELECT '61616161-0000-4000-8000-000000000003',
       (date_trunc('day', now() AT TIME ZONE 'UTC') + interval '17 hours'), NULL, NULL,
       st.id, pt.id, '12121212-0000-4000-8000-000000000001',
       '11111111-1111-1111-1111-111111111111'
FROM cat_operation_status st, cat_procedure_type pt
WHERE st.code = 'scheduled' AND pt.code = 'lap_chole';

INSERT INTO operation_patient (operation_id, patient_id) VALUES
  ('61616161-0000-4000-8000-000000000002', '31313131-0000-4000-8000-000000000001'),
  ('61616161-0000-4000-8000-000000000003', '31313131-0000-4000-8000-000000000001');

INSERT INTO operation_physician (operation_id, physician_id, surgical_role_id)
SELECT v.operation_id::uuid, '32323232-0000-4000-8000-000000000001', r.id
FROM (VALUES
  ('61616161-0000-4000-8000-000000000002'),
  ('61616161-0000-4000-8000-000000000003')
) AS v(operation_id)
JOIN cat_surgical_role r ON r.code = 'surgeon';

-- B) scheduled + aviso confirmado por SPD (capture_mode aun NULL hasta Start)
INSERT INTO work_session (
  id, started_at, ended_at, status_id, user_id, closed_by_user_id,
  operation_id, station_id, kit_id, current_phase_id, phase_changed_at,
  capture_mode, atypical_session, extended_retention, retention_until)
SELECT 'c1000002-0000-4000-8000-000000000001',
       NULL, NULL, ss.id,
       '22222222-2222-2222-2222-222222222221',
       NULL,
       '61616161-0000-4000-8000-000000000002',
       '13131313-0000-4000-8000-000000000001',
       '51515151-0000-4000-8000-000000000001',
       ph.id, NULL,
       NULL, FALSE, FALSE, NULL
FROM cat_session_status ss, cat_operation_phase ph
WHERE ss.code = 'scheduled' AND ph.code = 'start';

INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source)
SELECT 'c1000002-0000-4000-8000-000000000001', ki.family_id, ki.quantity, 'kit_snapshot'
FROM kit_item ki
WHERE ki.kit_id = '51515151-0000-4000-8000-000000000001';

-- C) scheduled sin aviso → al Start sera manual_no_privacy
INSERT INTO work_session (
  id, started_at, ended_at, status_id, user_id, closed_by_user_id,
  operation_id, station_id, kit_id, current_phase_id, phase_changed_at,
  capture_mode, atypical_session, extended_retention, retention_until)
SELECT 'c1000003-0000-4000-8000-000000000001',
       NULL, NULL, ss.id,
       '22222222-2222-2222-2222-222222222221',
       NULL,
       '61616161-0000-4000-8000-000000000003',
       '13131313-0000-4000-8000-000000000001',
       '51515151-0000-4000-8000-000000000001',
       ph.id, NULL,
       NULL, FALSE, FALSE, NULL
FROM cat_session_status ss, cat_operation_phase ph
WHERE ss.code = 'scheduled' AND ph.code = 'start';

INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source)
SELECT 'c1000003-0000-4000-8000-000000000001', ki.family_id, ki.quantity, 'kit_snapshot'
FROM kit_item ki
WHERE ki.kit_id = '51515151-0000-4000-8000-000000000001';

-- D) awaiting_spd_review (manual match) — cola Confirm close (SP-05)
INSERT INTO operation (id, scheduled_at, started_at, ended_at, status_id, procedure_type_id, room_id, institution_id)
SELECT '61616161-0000-4000-8000-000000000004',
       '2026-10-02T09:00:00Z', '2026-10-02T09:15:00Z', '2026-10-02T10:00:00Z',
       st.id, pt.id, '12121212-0000-4000-8000-000000000001',
       '11111111-1111-1111-1111-111111111111'
FROM cat_operation_status st, cat_procedure_type pt
WHERE st.code = 'closed' AND pt.code = 'lap_chole';

INSERT INTO work_session (
  id, started_at, ended_at, status_id, user_id, closed_by_user_id,
  operation_id, station_id, kit_id, current_phase_id, phase_changed_at,
  capture_mode, atypical_session, extended_retention, retention_until)
SELECT 'c1000004-0000-4000-8000-000000000001',
       '2026-10-02T09:20:00Z', '2026-10-02T09:55:00Z',
       ss.id,
       '22222222-2222-2222-2222-222222222221',
       NULL,
       '61616161-0000-4000-8000-000000000004',
       '13131313-0000-4000-8000-000000000001',
       '51515151-0000-4000-8000-000000000001',
       ph.id, '2026-10-02T09:50:00Z',
       'manual_no_privacy', FALSE, FALSE, NULL
FROM cat_session_status ss, cat_operation_phase ph
WHERE ss.code = 'awaiting_spd_review' AND ph.code = 'final_count';

INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source)
SELECT 'c1000004-0000-4000-8000-000000000001', ki.family_id, ki.quantity, 'kit_snapshot'
FROM kit_item ki
WHERE ki.kit_id = '51515151-0000-4000-8000-000000000001';

-- E) correction_required con discrepancia abierta — cola SP-06
INSERT INTO operation (id, scheduled_at, started_at, ended_at, status_id, procedure_type_id, room_id, institution_id)
SELECT '61616161-0000-4000-8000-000000000005',
       '2026-10-02T11:00:00Z', '2026-10-02T11:10:00Z', '2026-10-02T11:50:00Z',
       st.id, pt.id, '12121212-0000-4000-8000-000000000001',
       '11111111-1111-1111-1111-111111111111'
FROM cat_operation_status st, cat_procedure_type pt
WHERE st.code = 'closed' AND pt.code = 'lap_chole';

INSERT INTO work_session (
  id, started_at, ended_at, status_id, user_id, closed_by_user_id,
  operation_id, station_id, kit_id, current_phase_id, phase_changed_at,
  capture_mode, atypical_session, extended_retention, retention_until)
SELECT 'c1000005-0000-4000-8000-000000000001',
       '2026-10-02T11:15:00Z', '2026-10-02T11:45:00Z',
       ss.id,
       '22222222-2222-2222-2222-222222222221',
       NULL,
       '61616161-0000-4000-8000-000000000005',
       '13131313-0000-4000-8000-000000000001',
       '51515151-0000-4000-8000-000000000001',
       ph.id, '2026-10-02T11:40:00Z',
       'manual_no_privacy', FALSE, FALSE, NULL
FROM cat_session_status ss, cat_operation_phase ph
WHERE ss.code = 'correction_required' AND ph.code = 'final_count';

INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source)
SELECT 'c1000005-0000-4000-8000-000000000001', ki.family_id, ki.quantity, 'kit_snapshot'
FROM kit_item ki
WHERE ki.kit_id = '51515151-0000-4000-8000-000000000001';

-- Copia inmutable del kit al abrir la sesion A (cerrada historica).
INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source)
SELECT 'c1000001-0000-4000-8000-000000000001', ki.family_id, ki.quantity, 'kit_snapshot'
FROM kit_item ki
WHERE ki.kit_id = '51515151-0000-4000-8000-000000000001';

-- -----------------------------------------------------------------------------
-- 10. Eventos auditables de la sesion
-- -----------------------------------------------------------------------------
INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity,
                         payload, occurred_at, session_id, user_id)
SELECT v.id::uuid, et.id, f.id, v.expected, v.detected, v.payload::jsonb,
       v.occurred_at::timestamptz,
       'c1000001-0000-4000-8000-000000000001', v.user_id::uuid
FROM (VALUES
  ('71000001-0000-4000-8000-000000000001', 'session_open',       NULL,       NULL, NULL,
   '{"source": "station"}', '2026-09-05T16:02:11Z', '22222222-2222-2222-2222-222222222221'),
  ('71000001-0000-4000-8000-000000000002', 'phase_change',       NULL,       NULL, NULL,
   '{"from_phase": "start", "to_phase": "demo_phase_1", "source": "station_button"}',
   '2026-09-05T16:05:00Z', '22222222-2222-2222-2222-222222222221'),
  ('71000001-0000-4000-8000-000000000003', 'auto_count',         'POZZI',       1, 1,
   '{"model_version": "yolo26l-demo", "confidence_avg": 0.93}',
   '2026-09-05T16:06:30Z', NULL),
  ('71000001-0000-4000-8000-000000000004', 'auto_count',         'FARABEUF',    2, 1,
   '{"model_version": "yolo26l-demo", "confidence_avg": 0.89}',
   '2026-09-05T16:06:31Z', NULL),
  ('71000001-0000-4000-8000-000000000005', 'discrepancy_raised', 'FARABEUF',    2, 1,
   '{"rule": "expected_gt_detected"}', '2026-09-05T16:06:32Z', NULL),
  ('71000001-0000-4000-8000-000000000006', 'phase_change',       NULL,       NULL, NULL,
   '{"from_phase": "demo_phase_1", "to_phase": "demo_phase_2", "source": "station_button"}',
   '2026-09-05T17:40:00Z', '22222222-2222-2222-2222-222222222221'),
  ('71000001-0000-4000-8000-000000000007', 'close_blocked',      'FARABEUF',    2, 1,
   '{"reason": "unresolved_discrepancy"}', '2026-09-05T18:00:10Z',
   '22222222-2222-2222-2222-222222222221'),
  ('71000001-0000-4000-8000-000000000008', 'correction_applied', 'FARABEUF',    2, 1,
   '{"resolved_by_role": "spd_supervisor"}', '2026-09-05T18:04:00Z',
   '22222222-2222-2222-2222-222222222222'),
  ('71000001-0000-4000-8000-000000000009', 'phase_change',       NULL,       NULL, NULL,
   '{"from_phase": "demo_phase_2", "to_phase": "final_count", "source": "station_button"}',
   '2026-09-05T18:05:00Z', '22222222-2222-2222-2222-222222222222'),

  ('71000001-0000-4000-8000-00000000000a', 'session_close',      NULL,       NULL, NULL,
   '{"outcome": "closed_with_justified_discrepancy"}', '2026-09-05T18:12:40Z',
   '22222222-2222-2222-2222-222222222222')
) AS v(id, event_code, family_code, expected, detected, payload, occurred_at, user_id)
JOIN cat_event_type et ON et.code = v.event_code
LEFT JOIN instrument_family f ON f.code = v.family_code;

-- -----------------------------------------------------------------------------
-- 11. Discrepancia y su justificacion humana
--
-- Este es el caso que demuestra la regla de cierre: la sesion no pudo cerrarse
-- (evento close_blocked) hasta que una persona registro una justificacion.
-- -----------------------------------------------------------------------------
INSERT INTO discrepancy (id, description, resolved, resolved_at, reason_id, family_id,
                         expected_quantity, detected_quantity, session_id, origin_event_id)
SELECT '81000001-0000-4000-8000-000000000001',
       'Se esperaban 2 separadores Farabeuf conforme al kit y solo se detecto 1 en la charola.',
       TRUE, '2026-09-05T18:04:00Z',
       dr.id, f.id, 2, 1,
       'c1000001-0000-4000-8000-000000000001',
       '71000001-0000-4000-8000-000000000005'
FROM cat_discrepancy_reason dr, instrument_family f
WHERE dr.code = 'shortage' AND f.code = 'FARABEUF';

INSERT INTO human_correction (justification, recorded_at, count_event_id, user_id)
VALUES (
  'Verificacion fisica de la charola y del campo quirurgico. El inventario de la institucion cuenta con una sola pieza de esta familia disponible al momento de armar el kit; la segunda unidad se encuentra en esterilizacion. Se autoriza el cierre con la diferencia documentada y se solicita ajustar la plantilla del kit o reponer la pieza faltante.',
  '2026-09-05T18:04:00Z',
  '71000001-0000-4000-8000-000000000008',
  '22222222-2222-2222-2222-222222222222'
);

-- Eventos + conteos reportados para sesion D (match completo → awaiting review)
INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity,
                         payload, occurred_at, session_id, user_id)
SELECT '71000004-0000-4000-8000-000000000001'::uuid, et.id, NULL, NULL, NULL,
       '{"capture_mode":"manual_no_privacy","source":"station"}'::jsonb,
       '2026-10-02T09:20:00Z'::timestamptz,
       'c1000004-0000-4000-8000-000000000001',
       '22222222-2222-2222-2222-222222222221'
FROM cat_event_type et WHERE et.code = 'session_open';

INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity,
                         payload, occurred_at, session_id, user_id)
SELECT
  ('71000004-0000-4000-8000-00000000' || lpad((100 + row_number() OVER (ORDER BY f.code))::text, 4, '0'))::uuid,
  et.id, ki.family_id, ki.quantity, NULL,
  jsonb_build_object(
    'ai', false,
    'capture_mode', 'manual_no_privacy',
    'reported_quantity', ki.quantity
  ),
  '2026-10-02T09:50:00Z'::timestamptz + ((row_number() OVER (ORDER BY f.code)) || ' seconds')::interval,
  'c1000004-0000-4000-8000-000000000001',
  '22222222-2222-2222-2222-222222222221'
FROM kit_item ki
JOIN instrument_family f ON f.id = ki.family_id
CROSS JOIN cat_event_type et
WHERE ki.kit_id = '51515151-0000-4000-8000-000000000001' AND et.code = 'manual_count';

INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity,
                         payload, occurred_at, session_id, user_id)
SELECT '71000004-0000-4000-8000-000000000050'::uuid, et.id, NULL, NULL, NULL,
       '{"from_phase":"start","to_phase":"final_count","source":"station_button"}'::jsonb,
       '2026-10-02T09:52:00Z'::timestamptz,
       'c1000004-0000-4000-8000-000000000001',
       '22222222-2222-2222-2222-222222222221'
FROM cat_event_type et WHERE et.code = 'phase_change';

INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity,
                         payload, occurred_at, session_id, user_id)
SELECT '71000004-0000-4000-8000-000000000099'::uuid, et.id, NULL, NULL, NULL,
       '{"ai":false,"capture_mode":"manual_no_privacy","open_discrepancies":0,"next_status":"awaiting_spd_review"}'::jsonb,
       '2026-10-02T09:55:00Z'::timestamptz,
       'c1000004-0000-4000-8000-000000000001',
       '22222222-2222-2222-2222-222222222221'
FROM cat_event_type et WHERE et.code = 'manual_close';

-- Eventos + discrepancia abierta para sesion E (correction_required; Farabeuf shortfall)
INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity,
                         payload, occurred_at, session_id, user_id)
SELECT '71000005-0000-4000-8000-000000000001'::uuid, et.id, NULL, NULL, NULL,
       '{"capture_mode":"manual_no_privacy","source":"station"}'::jsonb,
       '2026-10-02T11:15:00Z'::timestamptz,
       'c1000005-0000-4000-8000-000000000001',
       '22222222-2222-2222-2222-222222222221'
FROM cat_event_type et WHERE et.code = 'session_open';

INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity,
                         payload, occurred_at, session_id, user_id)
SELECT
  ('71000005-0000-4000-8000-00000000' || lpad((100 + row_number() OVER (ORDER BY f.code))::text, 4, '0'))::uuid,
  et.id, ki.family_id, ki.quantity, NULL,
  CASE WHEN f.code = 'FARABEUF' THEN
    jsonb_build_object(
      'ai', false, 'capture_mode', 'manual_no_privacy',
      'reported_quantity', 1, 'reason_code', 'shortage',
      'notes', 'One Farabeuf missing from tray'
    )
  ELSE
    jsonb_build_object(
      'ai', false, 'capture_mode', 'manual_no_privacy',
      'reported_quantity', ki.quantity
    )
  END,
  '2026-10-02T11:40:00Z'::timestamptz + ((row_number() OVER (ORDER BY f.code)) || ' seconds')::interval,
  'c1000005-0000-4000-8000-000000000001',
  '22222222-2222-2222-2222-222222222221'
FROM kit_item ki
JOIN instrument_family f ON f.id = ki.family_id
CROSS JOIN cat_event_type et
WHERE ki.kit_id = '51515151-0000-4000-8000-000000000001' AND et.code = 'manual_count';

INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity,
                         payload, occurred_at, session_id, user_id)
SELECT '71000005-0000-4000-8000-000000000050'::uuid, et.id, f.id, 2, 1,
       '{"rule":"expected_gt_detected","source":"manual_report"}'::jsonb,
       '2026-10-02T11:44:30Z'::timestamptz,
       'c1000005-0000-4000-8000-000000000001',
       '22222222-2222-2222-2222-222222222221'
FROM cat_event_type et
JOIN instrument_family f ON f.code = 'FARABEUF'
WHERE et.code = 'discrepancy_raised';

INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity,
                         payload, occurred_at, session_id, user_id)
SELECT '71000005-0000-4000-8000-000000000099'::uuid, et.id, NULL, NULL, NULL,
       '{"ai":false,"capture_mode":"manual_no_privacy","open_discrepancies":1,"next_status":"correction_required"}'::jsonb,
       '2026-10-02T11:45:00Z'::timestamptz,
       'c1000005-0000-4000-8000-000000000001',
       '22222222-2222-2222-2222-222222222221'
FROM cat_event_type et WHERE et.code = 'manual_close';

INSERT INTO discrepancy (id, description, resolved, resolved_at, reason_id, family_id,
                         expected_quantity, detected_quantity, session_id, origin_event_id)
SELECT '81000005-0000-4000-8000-000000000001',
       'Manual report shortfall: Farabeuf expected 2, reported 1.',
       FALSE, NULL,
       dr.id, f.id, 2, 1,
       'c1000005-0000-4000-8000-000000000001',
       ce.id
FROM cat_discrepancy_reason dr, instrument_family f, count_event ce
WHERE dr.code = 'shortage' AND f.code = 'FARABEUF'
  AND ce.session_id = 'c1000005-0000-4000-8000-000000000001'
  AND ce.family_id = f.id
  AND ce.payload->>'reported_quantity' = '1'
LIMIT 1;

INSERT INTO human_correction (justification, recorded_at, count_event_id, user_id)
SELECT
  'shortage: One Farabeuf missing from tray',
  '2026-10-02T11:40:03Z',
  ce.id,
  '22222222-2222-2222-2222-222222222221'
FROM count_event ce
JOIN instrument_family f ON f.id = ce.family_id
WHERE ce.session_id = 'c1000005-0000-4000-8000-000000000001'
  AND f.code = 'FARABEUF'
  AND ce.payload->>'reported_quantity' = '1'
LIMIT 1;

-- -----------------------------------------------------------------------------
-- 12. Acuerdos de tratamiento (RF-SP-02: confirma spd_supervisor)
--
-- quality_ops es obligatorio. model_improvement es opt-in.
-- Sesion A (cerrada) y B (scheduled): con aviso. Sesion C: sin acuerdo.
-- -----------------------------------------------------------------------------
INSERT INTO session_processing_agreement (
  session_id, privacy_notice_version_id, purpose_quality_ops,
  purpose_model_improvement, agreed_at, agreed_by_user_id)
SELECT 'c1000001-0000-4000-8000-000000000001', pnv.id, TRUE, TRUE,
       '2026-09-05T16:00:00Z', '22222222-2222-2222-2222-222222222222'
FROM privacy_notice_version pnv WHERE pnv.version = 'v1.0';

INSERT INTO session_processing_agreement (
  session_id, privacy_notice_version_id, purpose_quality_ops,
  purpose_model_improvement, agreed_at, agreed_by_user_id)
SELECT 'c1000002-0000-4000-8000-000000000001', pnv.id, TRUE, FALSE,
       '2026-10-02T14:00:00Z', '22222222-2222-2222-2222-222222222222'
FROM privacy_notice_version pnv WHERE pnv.version = 'v1.0';

-- -----------------------------------------------------------------------------
-- 13. Bitacora de acceso
-- -----------------------------------------------------------------------------
INSERT INTO access_audit (occurred_at, actor_type, actor_user_id, action,
                          resource_type, resource_id, institution_id, outcome, ip)
VALUES
  ('2026-09-05T18:02:00Z', 'user', '22222222-2222-2222-2222-222222222222',
   'read', 'work_session', 'c1000001-0000-4000-8000-000000000001',
   '11111111-1111-1111-1111-111111111111', 'success', '10.20.0.34'),
  ('2026-09-05T18:03:10Z', 'user', '22222222-2222-2222-2222-222222222221',
   'read', 'patient', '31313131-0000-4000-8000-000000000001',
   '11111111-1111-1111-1111-111111111111', 'denied', '10.20.0.51');

-- -----------------------------------------------------------------------------
-- 12. Sesiones extra para KPIs vivos del dashboard (hoy + in_progress)
-- -----------------------------------------------------------------------------
INSERT INTO operation (id, scheduled_at, started_at, ended_at, status_id, procedure_type_id, room_id, institution_id)
SELECT '61616161-0000-4000-8000-000000000006',
       (now() AT TIME ZONE 'UTC') - interval '45 minutes',
       (now() AT TIME ZONE 'UTC') - interval '30 minutes',
       NULL,
       st.id, pt.id, '12121212-0000-4000-8000-000000000001',
       '11111111-1111-1111-1111-111111111111'
FROM cat_operation_status st, cat_procedure_type pt
WHERE st.code = 'in_progress' AND pt.code = 'lap_chole';

INSERT INTO operation_patient (operation_id, patient_id) VALUES
  ('61616161-0000-4000-8000-000000000006', '31313131-0000-4000-8000-000000000001');

INSERT INTO operation_physician (operation_id, physician_id, surgical_role_id)
SELECT '61616161-0000-4000-8000-000000000006', '32323232-0000-4000-8000-000000000001', r.id
FROM cat_surgical_role r WHERE r.code = 'surgeon';

INSERT INTO work_session (
  id, started_at, ended_at, status_id, user_id, closed_by_user_id,
  operation_id, station_id, kit_id, current_phase_id, phase_changed_at,
  capture_mode, atypical_session, extended_retention, retention_until)
SELECT 'c1000006-0000-4000-8000-000000000001',
       (now() AT TIME ZONE 'UTC') - interval '25 minutes', NULL,
       ss.id,
       '22222222-2222-2222-2222-222222222221',
       NULL,
       '61616161-0000-4000-8000-000000000006',
       '13131313-0000-4000-8000-000000000001',
       '51515151-0000-4000-8000-000000000001',
       ph.id, (now() AT TIME ZONE 'UTC') - interval '10 minutes',
       'vision', FALSE, FALSE, NULL
FROM cat_session_status ss, cat_operation_phase ph
WHERE ss.code = 'in_progress' AND ph.code = 'demo_phase_1';

INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source)
SELECT 'c1000006-0000-4000-8000-000000000001', ki.family_id, ki.quantity, 'kit_snapshot'
FROM kit_item ki
WHERE ki.kit_id = '51515151-0000-4000-8000-000000000001';

INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source)
SELECT 'c1000006-0000-4000-8000-000000000001', f.id, 1, 'live_add'
FROM instrument_family f WHERE f.code = 'PROBE'
ON CONFLICT (session_id, family_id) DO NOTHING;

INSERT INTO session_processing_agreement (
  session_id, privacy_notice_version_id, purpose_quality_ops,
  purpose_model_improvement, agreed_at, agreed_by_user_id)
SELECT 'c1000006-0000-4000-8000-000000000001', pnv.id, TRUE, FALSE,
       (now() AT TIME ZONE 'UTC') - interval '40 minutes',
       '22222222-2222-2222-2222-222222222222'
FROM privacy_notice_version pnv WHERE pnv.version = 'v1.0';

INSERT INTO count_event (event_type_id, family_id, expected_quantity, detected_quantity,
                         payload, occurred_at, session_id, user_id)
SELECT et.id, NULL, NULL, NULL,
       '{"from_phase":"start","to_phase":"demo_phase_1","source":"station_button"}'::jsonb,
       (now() AT TIME ZONE 'UTC') - interval '10 minutes',
       'c1000006-0000-4000-8000-000000000001',
       '22222222-2222-2222-2222-222222222221'
FROM cat_event_type et WHERE et.code = 'phase_change';

-- -----------------------------------------------------------------------------
-- YOLO model pin + class map (17 labels = PEF/Image Dataset/classes.txt order)
-- Pesos locales: apps/VisionWorker/weights/best.pt (NO subir a GitHub; *.pt gitignored)
-- -----------------------------------------------------------------------------
INSERT INTO yolo_model (id, version_tag, media_asset_id, checksum, active, published_at)
VALUES (
  '71717171-0000-4000-8000-000000000001',
  'yolo26l-demo',
  NULL,
  repeat('a', 64),
  TRUE,
  now()
);

INSERT INTO model_class (model_id, family_id, yolo_class_id)
SELECT '71717171-0000-4000-8000-000000000001', f.id, v.yolo_class_id
FROM (VALUES
  (0,  'ADSON_PLAIN'),
  (1,  'ADSON_TEETH'),
  (2,  'FOERSTER'),
  (3,  'ALLIS'),
  (4,  'KELLY_FORCEPS'),
  (5,  'ROCHESTER'),
  (6,  'POZZI'),
  (7,  'OLSENHEG'),
  (8,  'MAYOHEG'),
  (9,  'MAYO_CURVED'),
  (10, 'MAYO_STRAIGHT'),
  (11, 'FARABEUF'),
  (12, 'SCALPEL3'),
  (13, 'MOSQUITO'),
  (14, 'SCALPEL4'),
  (15, 'CRILE_WOOD'),
  (16, 'PROBE')
) AS v(yolo_class_id, family_code)
JOIN instrument_family f ON f.code = v.family_code;

COMMIT;

-- =============================================================================
-- Verificacion del escenario
-- =============================================================================
-- Disponibilidad por familia (debe reflejar el inventario por pieza):
--   SELECT f.code,
--          count(i.id) FILTER (
--            WHERE i.cycle_status_id = (
--              SELECT id FROM cat_instrument_cycle_status WHERE code = 'available')
--              AND NOT EXISTS (SELECT 1 FROM instrument_reservation r
--                              WHERE r.instrument_id = i.id AND r.active)
--          ) AS disponibles
--   FROM instrument_family f
--   LEFT JOIN instrument i ON i.family_id = f.id AND i.active
--   GROUP BY f.code ORDER BY f.code;
--
-- Esperado vs detectado en la sesion:
--   SELECT f.code, ei.expected_quantity, ce.detected_quantity
--   FROM expected_inventory ei
--   JOIN instrument_family f ON f.id = ei.family_id
--   LEFT JOIN count_event ce ON ce.session_id = ei.session_id
--        AND ce.family_id = ei.family_id
--        AND ce.event_type_id = (SELECT id FROM cat_event_type WHERE code = 'auto_count')
--   WHERE ei.session_id = 'c1000001-0000-4000-8000-000000000001';
--
-- Linea de tiempo de fases (reconstruida desde count_event, no desde Mongo):
--   SELECT ce.occurred_at, ce.payload ->> 'from_phase' AS de,
--          ce.payload ->> 'to_phase' AS a
--   FROM count_event ce
--   JOIN cat_event_type et ON et.id = ce.event_type_id
--   WHERE ce.session_id = 'c1000001-0000-4000-8000-000000000001'
--     AND et.code = 'phase_change'
--   ORDER BY ce.occurred_at;
