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
-- CONTRASENAS: los hashes son marcadores de posicion sin contrasena valida
-- asociada. Antes de usar el ambiente, generar hashes reales con la funcion
-- de derivacion que emplee el backend. No colocar contrasenas en claro aqui.
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
  ('13131313-0000-4000-8000-000000000001', 'Estacion cenital OR-01',
   '{"shape": "rect", "x": 240, "y": 120, "w": 1180, "h": 760, "frame_w": 1920, "frame_h": 1080}'::jsonb,
   TRUE, '12121212-0000-4000-8000-000000000001');

-- -----------------------------------------------------------------------------
-- 2. Roles y usuarios (los tres perfiles del apartado 2 del reporte)
-- -----------------------------------------------------------------------------
INSERT INTO role (id, code, description, institution_id) VALUES
  ('14141414-0000-4000-8000-000000000001', 'operator_cde',
   'Operador de Central de Esterilizacion. Ejecuta sesiones de conteo.',
   '11111111-1111-1111-1111-111111111111'),
  ('14141414-0000-4000-8000-000000000002', 'supervisor_quality',
   'Supervisor de Proceso o Calidad. Revisa discrepancias y aprueba cierres.',
   '11111111-1111-1111-1111-111111111111'),
  ('14141414-0000-4000-8000-000000000003', 'it_admin',
   'Administrador Tecnico. Gestiona catalogos, usuarios y configuracion.',
   '11111111-1111-1111-1111-111111111111');

INSERT INTO "user" (id, name, email, password_hash, active, ui_preferences, institution_id) VALUES
  ('22222222-2222-2222-2222-222222222221', 'Ana Perez',    'ana.perez@demo.local',
   'PLACEHOLDER_NO_VALIDA_REEMPLAZAR', TRUE,
   '{"theme": "light", "language": "es-MX"}'::jsonb, '11111111-1111-1111-1111-111111111111'),
  ('22222222-2222-2222-2222-222222222222', 'Luis Ramirez', 'luis.ramirez@demo.local',
   'PLACEHOLDER_NO_VALIDA_REEMPLAZAR', TRUE,
   '{"theme": "dark", "language": "es-MX"}'::jsonb, '11111111-1111-1111-1111-111111111111'),
  ('22222222-2222-2222-2222-222222222223', 'Sofia Duarte', 'sofia.duarte@demo.local',
   'PLACEHOLDER_NO_VALIDA_REEMPLAZAR', TRUE,
   '{"theme": "system", "language": "en"}'::jsonb, '11111111-1111-1111-1111-111111111111');

INSERT INTO user_role (user_id, role_id) VALUES
  ('22222222-2222-2222-2222-222222222221', '14141414-0000-4000-8000-000000000001'),
  ('22222222-2222-2222-2222-222222222222', '14141414-0000-4000-8000-000000000002'),
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
   '11111111-1111-1111-1111-111111111111');

INSERT INTO physician_specialty (physician_id, specialty_id)
SELECT '32323232-0000-4000-8000-000000000001', s.id
FROM cat_specialty s WHERE s.code = 'general_surgery';

-- Identificador externo simulado: asi llegaria el paciente desde un HIS.
INSERT INTO resource_identifier (resource_type, resource_id, system, value, use_code, active)
VALUES ('patient', '31313131-0000-4000-8000-000000000001',
        'https://his.demo.local/mrn', 'MRN-0098231', 'official', TRUE);

-- -----------------------------------------------------------------------------
-- 4. Familias de instrumental
-- -----------------------------------------------------------------------------
INSERT INTO instrument_family (id, code, name, category_id, identify_text, classify_text, function_text, active)
SELECT v.id::uuid, v.code, v.name, c.id, v.identify_text, v.classify_text, v.function_text, TRUE
FROM (VALUES
  ('41414141-0000-4000-8000-000000000001', 'KELLY',  'Pinza Kelly',              'hemostasis',
   'Pinza recta o curva con cremallera y ranuras transversales en la punta que no llegan al extremo.',
   'Instrumental de hemostasia. Se distingue de la Rochester por el estriado parcial.',
   'Ocluir vasos sanguineos de calibre pequeno y mediano durante la diseccion.'),
  ('41414141-0000-4000-8000-000000000002', 'METZ',   'Tijera Metzenbaum',        'cutting',
   'Tijera de hojas delgadas y cortas respecto a la longitud del mango.',
   'Instrumental de corte para tejido delicado, no para sutura.',
   'Cortar y disecar tejido fino sin danar estructuras adyacentes.'),
  ('41414141-0000-4000-8000-000000000003', 'MAYOHEG','Portaagujas Mayo-Hegar',   'suturing',
   'Similar a una pinza pero con puntas cortas, anchas y ranurado cruzado.',
   'Instrumental de sutura. Se diferencia de la pinza por el ranurado en rejilla.',
   'Sujetar la aguja con firmeza durante el paso de sutura.'),
  ('41414141-0000-4000-8000-000000000004', 'FARABEUF','Separador Farabeuf',      'retraction',
   'Lamina metalica doblada en ambos extremos, sin cremallera ni articulacion.',
   'Instrumental de separacion de uso manual, siempre en par.',
   'Retraer bordes de la herida para exponer el campo quirurgico.')
) AS v(id, code, name, category_code, identify_text, classify_text, function_text)
JOIN cat_instrument_category c ON c.code = v.category_code;

-- -----------------------------------------------------------------------------
-- 5. Piezas fisicas
--
-- El inventario NO se lleva como total agregado por familia: cada pieza es una
-- fila. La cantidad disponible se obtiene por consulta sobre el estado del
-- ciclo de vida y la ausencia de reserva activa.
--
-- Cantidades deliberadas para el escenario:
--   KELLY    8 piezas  -> el kit pide 6, la reserva tiene exito
--   METZ     3 piezas  -> el kit pide 2, la reserva tiene exito
--   MAYOHEG  2 piezas  -> el kit pide 2, queda al limite
--   FARABEUF 1 pieza   -> el kit pide 2, PROVOCA inventario insuficiente
-- -----------------------------------------------------------------------------
INSERT INTO instrument (internal_code, family_id, cycle_status_id, institution_id, active)
SELECT
  f.code || '-' || lpad(g::text, 3, '0'),
  f.id,
  s.id,
  '11111111-1111-1111-1111-111111111111',
  TRUE
FROM (VALUES ('KELLY', 8), ('METZ', 3), ('MAYOHEG', 2), ('FARABEUF', 1))
       AS q(family_code, units)
JOIN instrument_family f ON f.code = q.family_code
CROSS JOIN LATERAL generate_series(1, q.units) AS g
CROSS JOIN cat_instrument_cycle_status s
WHERE s.code = 'available';

-- -----------------------------------------------------------------------------
-- 6. Kit y su vinculo con el procedimiento
-- -----------------------------------------------------------------------------
INSERT INTO kit (id, name, version, active, institution_id) VALUES
  ('51515151-0000-4000-8000-000000000001', 'Kit laparoscopico basico', 1, TRUE,
   '11111111-1111-1111-1111-111111111111');

INSERT INTO kit_item (kit_id, family_id, quantity)
SELECT '51515151-0000-4000-8000-000000000001', f.id, v.qty
FROM (VALUES ('KELLY', 6), ('METZ', 2), ('MAYOHEG', 2), ('FARABEUF', 2))
       AS v(family_code, qty)
JOIN instrument_family f ON f.code = v.family_code;

INSERT INTO procedure_kit (procedure_type_id, kit_id, technique_label, is_default, active)
SELECT pt.id, '51515151-0000-4000-8000-000000000001', 'Laparoscopica', TRUE, TRUE
FROM cat_procedure_type pt WHERE pt.code = 'lap_chole';

-- -----------------------------------------------------------------------------
-- 7. Secuencia de fases del procedimiento
--
-- is_count_required marca las fases en las que el modulo de reglas exige
-- conteo completo antes de permitir avanzar.
-- -----------------------------------------------------------------------------
INSERT INTO procedure_phase (procedure_type_id, phase_id, sort_order, is_count_required, active)
SELECT pt.id, ph.id, v.ord, v.req, TRUE
FROM cat_procedure_type pt
CROSS JOIN (VALUES
  ('setup',        1, FALSE),
  ('pre_incision', 2, TRUE),
  ('intraop',      3, FALSE),
  ('pre_closure',  4, TRUE),
  ('closure',      5, FALSE),
  ('final_count',  6, TRUE),
  ('handover',     7, FALSE)
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
WHERE f.code IN ('KELLY', 'METZ', 'MAYOHEG', 'FARABEUF');

-- -----------------------------------------------------------------------------
-- 9. Sesion de conteo cerrada
-- -----------------------------------------------------------------------------
INSERT INTO work_session (
  id, started_at, ended_at, status_id, user_id, closed_by_user_id,
  operation_id, station_id, kit_id, current_phase_id, phase_changed_at,
  atypical_session, extended_retention, retention_until)
SELECT 'c1000001-0000-4000-8000-000000000001',
       '2026-09-05T16:02:11Z', '2026-09-05T18:12:40Z',
       ss.id,
       '22222222-2222-2222-2222-222222222221',   -- abierta por la operadora
       '22222222-2222-2222-2222-222222222222',   -- cerrada por el supervisor
       '61616161-0000-4000-8000-000000000001',
       '13131313-0000-4000-8000-000000000001',
       '51515151-0000-4000-8000-000000000001',
       ph.id, '2026-09-05T18:05:00Z',
       FALSE, FALSE, '2026-12-04T00:00:00Z'
FROM cat_session_status ss, cat_operation_phase ph
WHERE ss.code = 'closed' AND ph.code = 'final_count';

-- Copia inmutable del kit al abrir la sesion.
-- Esta copia conserva lo que se esperaba ese dia aunque el kit cambie despues.
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
   '{"from_phase": "setup", "to_phase": "pre_incision", "source": "station_button"}',
   '2026-09-05T16:05:00Z', '22222222-2222-2222-2222-222222222221'),
  ('71000001-0000-4000-8000-000000000003', 'auto_count',         'KELLY',       6, 6,
   '{"model_version": "yolo-v2.1.0", "confidence_avg": 0.93}',
   '2026-09-05T16:06:30Z', NULL),
  ('71000001-0000-4000-8000-000000000004', 'auto_count',         'FARABEUF',    2, 1,
   '{"model_version": "yolo-v2.1.0", "confidence_avg": 0.89}',
   '2026-09-05T16:06:31Z', NULL),
  ('71000001-0000-4000-8000-000000000005', 'discrepancy_raised', 'FARABEUF',    2, 1,
   '{"rule": "expected_gt_detected"}', '2026-09-05T16:06:32Z', NULL),
  ('71000001-0000-4000-8000-000000000006', 'phase_change',       NULL,       NULL, NULL,
   '{"from_phase": "pre_incision", "to_phase": "pre_closure", "source": "station_button"}',
   '2026-09-05T17:40:00Z', '22222222-2222-2222-2222-222222222221'),
  ('71000001-0000-4000-8000-000000000007', 'close_blocked',      'FARABEUF',    2, 1,
   '{"reason": "unresolved_discrepancy"}', '2026-09-05T18:00:10Z',
   '22222222-2222-2222-2222-222222222221'),
  ('71000001-0000-4000-8000-000000000008', 'correction_applied', 'FARABEUF',    2, 1,
   '{"resolved_by_role": "supervisor_quality"}', '2026-09-05T18:04:00Z',
   '22222222-2222-2222-2222-222222222222'),
  ('71000001-0000-4000-8000-000000000009', 'phase_change',       NULL,       NULL, NULL,
   '{"from_phase": "pre_closure", "to_phase": "final_count", "source": "station_button"}',
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

-- -----------------------------------------------------------------------------
-- 12. Acuerdo de tratamiento de la sesion
--
-- quality_ops es obligatorio (lo exige un CHECK del esquema).
-- model_improvement es opt-in por sesion: aqui se marca aceptado para que el
-- ambiente de desarrollo tenga al menos una sesion elegible para exportacion
-- de entrenamiento.
-- -----------------------------------------------------------------------------
INSERT INTO session_processing_agreement (
  session_id, privacy_notice_version_id, purpose_quality_ops,
  purpose_model_improvement, agreed_at, agreed_by_user_id)
SELECT 'c1000001-0000-4000-8000-000000000001', pnv.id, TRUE, TRUE,
       '2026-09-05T16:02:11Z', '22222222-2222-2222-2222-222222222221'
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
