-- =============================================================================
-- PEF · 004_seed_e2e.sql
-- Escenario minimo para la prueba end-to-end del flujo ACTUAL del monolito:
--   Admin -> Operator (New Counting Session -> Capture -> Controlled inference ->
--   Human Validation) -> Supervisor (Request Correction / Approve) -> Close.
--
-- AMBITO: SOLO desarrollo / pruebas locales. NO ejecutar en produccion.
-- Orden: 001_init.sql -> 002_seed_catalogs.sql -> 004_seed_e2e.sql
-- (003_seed_demo.sql es un escenario historico independiente y no es necesario).
--
-- IDIOMA: todos los valores persistidos estan en ingles (idioma canonico de la
-- base de datos); la localizacion al espanol se hace en la capa de aplicacion.
-- Las bases ya sembradas con la version anterior (en espanol) se convierten con
-- data/migrations/005_canonical_english_data.sql.
--
-- IDEMPOTENTE: IDs fijos del escenario E2E + ON CONFLICT. Puede re-ejecutarse;
-- los usuarios E2E recuperan su contrasena y quedan activos.
--
-- CREDENCIALES DE PRUEBA (no usar en produccion). Contrasena de los tres usuarios:
--   E2eTest#2026
--   admin.e2e@pef.local       -> it_admin
--   operator.e2e@pef.local    -> operator_cde
--   supervisor.e2e@pef.local  -> supervisor_quality
-- Los hashes son werkzeug pbkdf2:sha256 (compatibles con BackendAuthService);
-- la contrasena nunca se guarda en texto plano.
--
-- YoloModel: registro de desarrollo para el provider `controlled`. NO es un
-- modelo entrenado y no tiene pesos (media_asset_id NULL). Al activarse,
-- desactiva cualquier otro modelo activo (indice unico uk_yolo_model_active).
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Institucion, quirofano y estacion
-- -----------------------------------------------------------------------------
INSERT INTO institution (id, name, active) VALUES
  ('e2e00000-0000-4000-8000-000000000001', 'Hospital E2E (testing)', TRUE)
ON CONFLICT (id) DO UPDATE SET active = TRUE;

INSERT INTO operating_room (id, code, name, active, institution_id) VALUES
  ('e2e00000-0000-4000-8000-000000000011', 'E2E-OR-1', 'E2E Operating Room 1', TRUE,
   'e2e00000-0000-4000-8000-000000000001')
ON CONFLICT DO NOTHING;

INSERT INTO capture_station (id, name, roi, active, room_id) VALUES
  ('e2e00000-0000-4000-8000-000000000012', 'Overhead Capture Station E2E-OR-1', NULL, TRUE,
   'e2e00000-0000-4000-8000-000000000011')
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 2. Roles (codigos usados por la aplicacion) y usuarios de prueba
-- -----------------------------------------------------------------------------
INSERT INTO role (id, code, description, institution_id) VALUES
  ('e2e00000-0000-4000-8000-000000000021', 'it_admin', 'IT Administrator',
   'e2e00000-0000-4000-8000-000000000001'),
  ('e2e00000-0000-4000-8000-000000000022', 'operator_cde', 'Operator CDE',
   'e2e00000-0000-4000-8000-000000000001'),
  ('e2e00000-0000-4000-8000-000000000023', 'supervisor_quality', 'Supervisor CDE / Quality',
   'e2e00000-0000-4000-8000-000000000001')
ON CONFLICT DO NOTHING;

INSERT INTO "user" (id, name, email, password_hash, active, institution_id) VALUES
  ('e2e00000-0000-4000-8000-000000000031', 'E2E Administrator', 'admin.e2e@pef.local',
   'pbkdf2:sha256:600000$cXPIwtgBPt4JlEwV$9c81161e610f4b55e615bdf812b27314fb7629704f4c70cc6742576302be96f6',
   TRUE, 'e2e00000-0000-4000-8000-000000000001'),
  ('e2e00000-0000-4000-8000-000000000032', 'E2E Operator', 'operator.e2e@pef.local',
   'pbkdf2:sha256:600000$2fYdUIwbay4vzoIB$ed967825121be50ef5157b2d5fc1a7623b9a530ea83e524760e40e61114b83f8',
   TRUE, 'e2e00000-0000-4000-8000-000000000001'),
  ('e2e00000-0000-4000-8000-000000000033', 'E2E Supervisor', 'supervisor.e2e@pef.local',
   'pbkdf2:sha256:600000$EXEiWr5Xz5TF67Vf$56597e8fe675a50a6700960410812c40f19268ba3bc3769554193934554a7e26',
   TRUE, 'e2e00000-0000-4000-8000-000000000001')
ON CONFLICT (id) DO UPDATE SET password_hash = EXCLUDED.password_hash, active = TRUE;

INSERT INTO user_role (user_id, role_id) VALUES
  ('e2e00000-0000-4000-8000-000000000031', 'e2e00000-0000-4000-8000-000000000021'),
  ('e2e00000-0000-4000-8000-000000000032', 'e2e00000-0000-4000-8000-000000000022'),
  ('e2e00000-0000-4000-8000-000000000033', 'e2e00000-0000-4000-8000-000000000023')
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 3. Familias de instrumental (catalogo global; se reutilizan si ya existen)
-- -----------------------------------------------------------------------------
INSERT INTO instrument_family (code, name, category_id, function_text, active)
SELECT v.code, v.name, c.id, v.function_text, TRUE
FROM (VALUES
  ('KELLY',    'Kelly Forceps',            'hemostasis', 'Occlude small and medium-caliber vessels.'),
  ('MOSQUITO', 'Mosquito Forceps',         'hemostasis', 'Hemostasis of fine vessels.'),
  ('METZ',     'Metzenbaum Scissors',      'cutting',    'Cutting and dissection of delicate tissue.'),
  ('MAYOHEG',  'Mayo-Hegar Needle Holder', 'suturing',   'Hold the needle while suturing.'),
  ('FARABEUF', 'Farabeuf Retractor',       'retraction', 'Retract superficial tissue planes.')
) AS v(code, name, category_code, function_text)
JOIN cat_instrument_category c ON c.code = v.category_code
ON CONFLICT (code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 4. Kit E2E y su composicion (origen del ExpectedInventory snapshot)
-- -----------------------------------------------------------------------------
INSERT INTO kit (id, name, version, active, institution_id) VALUES
  ('e2e00000-0000-4000-8000-000000000041', 'General Surgery Kit E2E', 1, TRUE,
   'e2e00000-0000-4000-8000-000000000001')
ON CONFLICT DO NOTHING;

INSERT INTO kit_item (kit_id, family_id, quantity)
SELECT 'e2e00000-0000-4000-8000-000000000041', f.id, v.qty
FROM (VALUES ('KELLY', 6), ('MOSQUITO', 8), ('METZ', 2), ('MAYOHEG', 2), ('FARABEUF', 2))
       AS v(family_code, qty)
JOIN instrument_family f ON f.code = v.family_code
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 5. Procedimiento: kit asociado (ProcedureKit) y fases de conteo (ProcedurePhase)
--    Procedimiento del catalogo 002: 'appendectomy'.
-- -----------------------------------------------------------------------------
-- default solo si el procedimiento aun no tiene un kit default activo (uk_procedure_kit_default)
INSERT INTO procedure_kit (procedure_type_id, kit_id, technique_label, is_default, active)
SELECT pt.id, 'e2e00000-0000-4000-8000-000000000041', 'Open (E2E)',
       NOT EXISTS (SELECT 1 FROM procedure_kit d
                   WHERE d.procedure_type_id = pt.id AND d.is_default AND d.active),
       TRUE
FROM cat_procedure_type pt WHERE pt.code = 'appendectomy'
ON CONFLICT DO NOTHING;

INSERT INTO procedure_phase (procedure_type_id, phase_id, sort_order, is_count_required, active)
SELECT pt.id, ph.id, v.ord, TRUE, TRUE
FROM cat_procedure_type pt
CROSS JOIN (VALUES ('pre_incision', 1), ('pre_closure', 2), ('final_count', 3)) AS v(phase_code, ord)
JOIN cat_operation_phase ph ON ph.code = v.phase_code
WHERE pt.code = 'appendectomy'
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 6. Paciente, medico y operacion programada (visible en New Counting Session)
-- -----------------------------------------------------------------------------
INSERT INTO patient (id, display_name, birth_date, active, gender_id, institution_id)
SELECT 'e2e00000-0000-4000-8000-000000000051', 'E2E Patient 001', DATE '1990-01-01', TRUE,
       g.id, 'e2e00000-0000-4000-8000-000000000001'
FROM cat_gender g WHERE g.code = 'unknown'
ON CONFLICT DO NOTHING;

INSERT INTO physician (id, name, active, institution_id) VALUES
  ('e2e00000-0000-4000-8000-000000000052', 'Dr. E2E Surgeon', TRUE,
   'e2e00000-0000-4000-8000-000000000001')
ON CONFLICT DO NOTHING;

INSERT INTO physician_specialty (physician_id, specialty_id)
SELECT 'e2e00000-0000-4000-8000-000000000052', s.id
FROM cat_specialty s WHERE s.code = 'general_surgery'
ON CONFLICT DO NOTHING;

INSERT INTO operation (id, scheduled_at, status_id, procedure_type_id, room_id, institution_id)
SELECT 'e2e00000-0000-4000-8000-000000000061', now(), st.id, pt.id,
       'e2e00000-0000-4000-8000-000000000011', 'e2e00000-0000-4000-8000-000000000001'
FROM cat_operation_status st, cat_procedure_type pt
WHERE st.code = 'scheduled' AND pt.code = 'appendectomy'
ON CONFLICT DO NOTHING;

INSERT INTO operation_patient (operation_id, patient_id) VALUES
  ('e2e00000-0000-4000-8000-000000000061', 'e2e00000-0000-4000-8000-000000000051')
ON CONFLICT DO NOTHING;

INSERT INTO operation_physician (operation_id, physician_id, surgical_role_id)
SELECT 'e2e00000-0000-4000-8000-000000000061', 'e2e00000-0000-4000-8000-000000000052', r.id
FROM cat_surgical_role r WHERE r.code = 'surgeon'
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 7. YoloModel de desarrollo (provider `controlled`) + mapeo de clases
-- -----------------------------------------------------------------------------
UPDATE yolo_model SET active = FALSE
WHERE active AND id <> 'e2e00000-0000-4000-8000-000000000071';

INSERT INTO yolo_model (id, version_tag, media_asset_id, checksum, active) VALUES
  ('e2e00000-0000-4000-8000-000000000071', 'e2e-controlled-dev', NULL, NULL, TRUE)
ON CONFLICT (id) DO UPDATE SET active = TRUE;

INSERT INTO model_class (model_id, family_id, yolo_class_id)
SELECT 'e2e00000-0000-4000-8000-000000000071', f.id, v.class_id
FROM (VALUES (0, 'KELLY'), (1, 'MOSQUITO'), (2, 'METZ'), (3, 'MAYOHEG'), (4, 'FARABEUF'))
       AS v(class_id, family_code)
JOIN instrument_family f ON f.code = v.family_code
ON CONFLICT DO NOTHING;

COMMIT;

-- Verificacion rapida (opcional):
-- SELECT o.id, st.code FROM operation o JOIN cat_operation_status st ON st.id = o.status_id
--  WHERE o.id = 'e2e00000-0000-4000-8000-000000000061';                 -- scheduled
-- SELECT version_tag, active FROM yolo_model WHERE active;               -- e2e-controlled-dev
-- SELECT yolo_class_id, f.code FROM model_class mc JOIN instrument_family f ON f.id = mc.family_id
--  WHERE mc.model_id = 'e2e00000-0000-4000-8000-000000000071' ORDER BY 1;
