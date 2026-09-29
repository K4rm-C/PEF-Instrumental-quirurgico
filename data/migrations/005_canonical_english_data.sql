-- =============================================================================
-- PEF · 005_canonical_english_data.sql
-- Converts databases seeded with the former Spanish display values
-- (002_seed_catalogs.sql, 003_seed_demo.sql family texts, 004_seed_e2e.sql)
-- to the canonical English values.
--
-- Rule: English is the canonical language for persisted database business data
-- and catalog data. UI localization is handled at the application layer.
--
-- Safety:
--   * DATA-ONLY: no DDL, no DELETE, no UUID / code / FK changes.
--   * Rows are located by stable identifiers (catalog `code`, instrument family
--     `code`, fixed E2E UUIDs, role `code` + E2E institution) AND the value is
--     only replaced while it still equals the original Spanish seed text. Values
--     edited by users are therefore left untouched.
--   * Idempotent: a second run (or a run on a fresh English install) updates 0 rows.
--   * Runs in one transaction.
--
-- Usage: psql -d <db> -v ON_ERROR_STOP=1 -f data/migrations/005_canonical_english_data.sql
-- =============================================================================

BEGIN;

CREATE TEMP TABLE canonical_english (
  table_name  text NOT NULL,
  code        text NOT NULL,
  old_value   text NOT NULL,
  new_value   text NOT NULL
) ON COMMIT DROP;

-- -----------------------------------------------------------------------------
-- Catalog names (002_seed_catalogs.sql), keyed by code
-- -----------------------------------------------------------------------------
INSERT INTO canonical_english (table_name, code, old_value, new_value) VALUES
  ('cat_gender', 'female',  'Femenino',        'Female'),
  ('cat_gender', 'male',    'Masculino',       'Male'),
  ('cat_gender', 'other',   'Otro',            'Other'),
  ('cat_gender', 'unknown', 'No especificado', 'Unspecified'),

  ('cat_specialty', 'general_surgery', 'Cirugia general',           'General Surgery'),
  ('cat_specialty', 'orthopedics',     'Traumatologia y ortopedia', 'Orthopedics and Traumatology'),
  ('cat_specialty', 'gynecology',      'Ginecologia y obstetricia', 'Gynecology and Obstetrics'),
  ('cat_specialty', 'urology',         'Urologia',                  'Urology'),
  ('cat_specialty', 'neurosurgery',    'Neurocirugia',              'Neurosurgery'),
  ('cat_specialty', 'anesthesiology',  'Anestesiologia',            'Anesthesiology'),

  ('cat_procedure_type', 'lap_chole',     'Colecistectomia laparoscopica', 'Laparoscopic Cholecystectomy'),
  ('cat_procedure_type', 'open_chole',    'Colecistectomia abierta',       'Open Cholecystectomy'),
  ('cat_procedure_type', 'appendectomy',  'Apendicectomia',                'Appendectomy'),
  ('cat_procedure_type', 'hernia_repair', 'Plastia inguinal',              'Inguinal Hernia Repair'),
  ('cat_procedure_type', 'cesarean',      'Cesarea',                       'Cesarean Section'),
  ('cat_procedure_type', 'hip_replace',   'Artroplastia de cadera',        'Hip Arthroplasty'),

  ('cat_surgical_role', 'surgeon',          'Cirujano',         'Surgeon'),
  ('cat_surgical_role', 'first_assistant',  'Primer ayudante',  'First Assistant'),
  ('cat_surgical_role', 'anesthesiologist', 'Anestesiologo',    'Anesthesiologist'),
  ('cat_surgical_role', 'resident',         'Medico residente', 'Resident Physician'),

  ('cat_operation_status', 'scheduled',   'Programada', 'Scheduled'),
  ('cat_operation_status', 'in_progress', 'En curso',   'In Progress'),
  ('cat_operation_status', 'closed',      'Cerrada',    'Closed'),
  ('cat_operation_status', 'cancelled',   'Cancelada',  'Cancelled'),

  ('cat_session_status', 'open',       'Abierta',                    'Open'),
  ('cat_session_status', 'counting',   'En conteo',                  'Counting'),
  ('cat_session_status', 'validating', 'En validacion',              'Validating'),
  ('cat_session_status', 'blocked',    'Bloqueada por discrepancia', 'Blocked by Discrepancy'),
  ('cat_session_status', 'closed',     'Cerrada',                    'Closed'),
  ('cat_session_status', 'cancelled',  'Cancelada',                  'Cancelled'),

  ('cat_instrument_cycle_status', 'available',     'Disponible',        'Available'),
  ('cat_instrument_cycle_status', 'reserved',      'Reservada',         'Reserved'),
  ('cat_instrument_cycle_status', 'in_use',        'En uso',            'In Use'),
  ('cat_instrument_cycle_status', 'sterilization', 'En esterilizacion', 'In Sterilization'),
  ('cat_instrument_cycle_status', 'maintenance',   'En mantenimiento',  'In Maintenance'),
  ('cat_instrument_cycle_status', 'retired',       'Retirada',          'Retired'),

  ('cat_instrument_category', 'hemostasis', 'Hemostasia', 'Hemostasis'),
  ('cat_instrument_category', 'cutting',    'Corte',      'Cutting'),
  ('cat_instrument_category', 'dissection', 'Diseccion',  'Dissection'),
  ('cat_instrument_category', 'retraction', 'Separacion', 'Retraction'),
  ('cat_instrument_category', 'grasping',   'Prension',   'Grasping'),
  ('cat_instrument_category', 'suturing',   'Sutura',     'Suturing'),
  ('cat_instrument_category', 'suction',    'Aspiracion', 'Suction'),

  ('cat_usage_context', 'operative',   'Uso operativo en acto quirurgico',  'Operative use during surgery'),
  ('cat_usage_context', 'pedagogical', 'Uso pedagogico o de entrenamiento', 'Teaching or training use'),

  ('cat_discrepancy_reason', 'shortage',       'Faltante respecto al inventario esperado',      'Shortage against expected inventory'),
  ('cat_discrepancy_reason', 'surplus',        'Sobrante respecto al inventario esperado',      'Surplus against expected inventory'),
  ('cat_discrepancy_reason', 'unidentified',   'Pieza presente no identificada por el modelo',  'Item present but not identified by the model'),
  ('cat_discrepancy_reason', 'occluded',       'Pieza ocluida o fuera de la region de interes', 'Item occluded or outside the region of interest'),
  ('cat_discrepancy_reason', 'misclassified',  'Pieza clasificada en familia incorrecta',       'Item classified into the wrong family'),
  ('cat_discrepancy_reason', 'operator_error', 'Error de captura o manipulacion del operador',  'Operator capture or handling error'),
  ('cat_discrepancy_reason', 'low_confidence', 'Confianza del modelo por debajo del umbral',    'Model confidence below threshold'),

  ('cat_checkpoint_reason', 'start',        'Inicio de sesion',           'Session start'),
  ('cat_checkpoint_reason', 'close',        'Cierre de sesion',           'Session close'),
  ('cat_checkpoint_reason', 'hourly',       'Captura periodica',          'Periodic capture'),
  ('cat_checkpoint_reason', 'state_change', 'Cambio de estado relevante', 'Relevant state change'),
  ('cat_checkpoint_reason', 'discrepancy',  'Discrepancia registrada',    'Discrepancy recorded'),
  ('cat_checkpoint_reason', 'manual_pin',   'Marca manual del operador',  'Operator manual pin'),

  ('cat_operation_phase', 'setup',        'Preparacion de mesa',               'Table Setup'),
  ('cat_operation_phase', 'pre_incision', 'Conteo inicial previo a incision',  'Initial Count Before Incision'),
  ('cat_operation_phase', 'intraop',      'Transoperatorio',                   'Intraoperative'),
  ('cat_operation_phase', 'pre_closure',  'Conteo previo a cierre de cavidad', 'Count Before Cavity Closure'),
  ('cat_operation_phase', 'closure',      'Cierre',                            'Closure'),
  ('cat_operation_phase', 'final_count',  'Conteo final',                      'Final Count'),
  ('cat_operation_phase', 'handover',     'Entrega y retiro de charola',       'Tray Handover and Removal'),

  ('cat_event_type', 'session_open',         'Apertura de sesion',                   'Session opened'),
  ('cat_event_type', 'auto_count',           'Conteo sugerido por el modelo',        'Model-suggested count'),
  ('cat_event_type', 'manual_count',         'Conteo capturado manualmente',         'Manual count'),
  ('cat_event_type', 'phase_change',         'Cambio de fase quirurgica',            'Surgical phase change'),
  ('cat_event_type', 'discrepancy_raised',   'Discrepancia detectada',               'Discrepancy raised'),
  ('cat_event_type', 'correction_applied',   'Correccion humana aplicada',           'Human correction applied'),
  ('cat_event_type', 'validation_passed',    'Validacion sin discrepancias',         'Validation without discrepancies'),
  ('cat_event_type', 'close_blocked',        'Intento de cierre bloqueado',          'Close attempt blocked'),
  ('cat_event_type', 'session_close',        'Cierre de sesion',                     'Session closed'),
  ('cat_event_type', 'correction_requested', 'Correccion solicitada por supervisor', 'Correction requested by supervisor'),

  ('cat_processing_purpose', 'quality_ops',       'Calidad y operacion del proceso de conteo', 'Counting process quality and operations'),
  ('cat_processing_purpose', 'model_improvement', 'Mejora del modelo de vision',               'Vision model improvement'),
  ('cat_processing_purpose', 'external_sharing',  'Transferencia a terceros',                  'Third-party data sharing');

UPDATE cat_gender AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_gender' AND t.code = m.code AND t.name = m.old_value;
UPDATE cat_specialty AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_specialty' AND t.code = m.code AND t.name = m.old_value;
UPDATE cat_procedure_type AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_procedure_type' AND t.code = m.code AND t.name = m.old_value;
UPDATE cat_surgical_role AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_surgical_role' AND t.code = m.code AND t.name = m.old_value;
UPDATE cat_operation_status AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_operation_status' AND t.code = m.code AND t.name = m.old_value;
UPDATE cat_session_status AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_session_status' AND t.code = m.code AND t.name = m.old_value;
UPDATE cat_instrument_cycle_status AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_instrument_cycle_status' AND t.code = m.code AND t.name = m.old_value;
UPDATE cat_instrument_category AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_instrument_category' AND t.code = m.code AND t.name = m.old_value;
UPDATE cat_usage_context AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_usage_context' AND t.code = m.code AND t.name = m.old_value;
UPDATE cat_discrepancy_reason AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_discrepancy_reason' AND t.code = m.code AND t.name = m.old_value;
UPDATE cat_checkpoint_reason AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_checkpoint_reason' AND t.code = m.code AND t.name = m.old_value;
UPDATE cat_operation_phase AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_operation_phase' AND t.code = m.code AND t.name = m.old_value;
UPDATE cat_event_type AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_event_type' AND t.code = m.code AND t.name = m.old_value;
UPDATE cat_processing_purpose AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'cat_processing_purpose' AND t.code = m.code AND t.name = m.old_value;

-- Placeholder privacy notice URI (only while it is still the original placeholder)
UPDATE privacy_notice_version
   SET document_uri = 'https://pef.udem/overhead-count/privacy-notice/v1.0'
 WHERE version = 'v1.0' AND document_uri = 'https://pef.udem/conteo-cenital/aviso-privacidad/v1.0';

-- -----------------------------------------------------------------------------
-- Instrument families (global catalog), keyed by family code.
-- Covers the texts of both 003_seed_demo.sql and 004_seed_e2e.sql.
-- -----------------------------------------------------------------------------
INSERT INTO canonical_english (table_name, code, old_value, new_value) VALUES
  ('family.name', 'KELLY',    'Pinza Kelly',            'Kelly Forceps'),
  ('family.name', 'MOSQUITO', 'Pinza Mosquito',         'Mosquito Forceps'),
  ('family.name', 'METZ',     'Tijera Metzenbaum',      'Metzenbaum Scissors'),
  ('family.name', 'MAYOHEG',  'Portaagujas Mayo-Hegar', 'Mayo-Hegar Needle Holder'),
  ('family.name', 'FARABEUF', 'Separador Farabeuf',     'Farabeuf Retractor'),

  ('family.function_text', 'KELLY',    'Ocluir vasos de calibre pequeno y mediano.', 'Occlude small and medium-caliber vessels.'),
  ('family.function_text', 'MOSQUITO', 'Hemostasia de vasos finos.',                 'Hemostasis of fine vessels.'),
  ('family.function_text', 'METZ',     'Corte y diseccion de tejido delicado.',      'Cutting and dissection of delicate tissue.'),
  ('family.function_text', 'MAYOHEG',  'Sujetar la aguja durante la sutura.',        'Hold the needle while suturing.'),
  ('family.function_text', 'FARABEUF', 'Separar planos superficiales.',              'Retract superficial tissue planes.'),
  ('family.function_text', 'KELLY',    'Ocluir vasos sanguineos de calibre pequeno y mediano durante la diseccion.',
                                       'Occlude small and medium-caliber blood vessels during dissection.'),
  ('family.function_text', 'METZ',     'Cortar y disecar tejido fino sin danar estructuras adyacentes.',
                                       'Cut and dissect fine tissue without damaging adjacent structures.'),
  ('family.function_text', 'MAYOHEG',  'Sujetar la aguja con firmeza durante el paso de sutura.',
                                       'Hold the needle firmly while passing the suture.'),
  ('family.function_text', 'FARABEUF', 'Retraer bordes de la herida para exponer el campo quirurgico.',
                                       'Retract the wound edges to expose the surgical field.'),

  ('family.identify_text', 'KELLY',    'Pinza recta o curva con cremallera y ranuras transversales en la punta que no llegan al extremo.',
                                       'Straight or curved clamp with a ratchet and transverse serrations at the tip that do not reach the end.'),
  ('family.identify_text', 'METZ',     'Tijera de hojas delgadas y cortas respecto a la longitud del mango.',
                                       'Scissors with thin blades that are short relative to the handle length.'),
  ('family.identify_text', 'MAYOHEG',  'Similar a una pinza pero con puntas cortas, anchas y ranurado cruzado.',
                                       'Similar to a clamp but with short, wide jaws and cross-hatched serrations.'),
  ('family.identify_text', 'FARABEUF', 'Lamina metalica doblada en ambos extremos, sin cremallera ni articulacion.',
                                       'Metal blade bent at both ends, without ratchet or joint.'),

  ('family.classify_text', 'KELLY',    'Instrumental de hemostasia. Se distingue de la Rochester por el estriado parcial.',
                                       'Hemostasis instrument. Distinguished from the Rochester clamp by its partial serrations.'),
  ('family.classify_text', 'METZ',     'Instrumental de corte para tejido delicado, no para sutura.',
                                       'Cutting instrument for delicate tissue, not for sutures.'),
  ('family.classify_text', 'MAYOHEG',  'Instrumental de sutura. Se diferencia de la pinza por el ranurado en rejilla.',
                                       'Suturing instrument. Differs from a clamp by its grid serrations.'),
  ('family.classify_text', 'FARABEUF', 'Instrumental de separacion de uso manual, siempre en par.',
                                       'Hand-held retraction instrument, always used in pairs.');

UPDATE instrument_family AS t SET name = m.new_value FROM canonical_english m
 WHERE m.table_name = 'family.name' AND t.code = m.code AND t.name = m.old_value;
UPDATE instrument_family AS t SET function_text = m.new_value FROM canonical_english m
 WHERE m.table_name = 'family.function_text' AND t.code = m.code AND t.function_text = m.old_value;
UPDATE instrument_family AS t SET identify_text = m.new_value FROM canonical_english m
 WHERE m.table_name = 'family.identify_text' AND t.code = m.code AND t.identify_text = m.old_value;
UPDATE instrument_family AS t SET classify_text = m.new_value FROM canonical_english m
 WHERE m.table_name = 'family.classify_text' AND t.code = m.code AND t.classify_text = m.old_value;

-- -----------------------------------------------------------------------------
-- E2E scenario (004_seed_e2e.sql), keyed by its fixed UUIDs
-- -----------------------------------------------------------------------------
UPDATE operating_room SET name = 'E2E Operating Room 1'
 WHERE id = 'e2e00000-0000-4000-8000-000000000011' AND name = 'Quirofano E2E 1';

UPDATE capture_station SET name = 'Overhead Capture Station E2E-OR-1'
 WHERE id = 'e2e00000-0000-4000-8000-000000000012' AND name = 'Estacion cenital E2E-OR-1';

UPDATE role SET description = 'IT Administrator'
 WHERE id = 'e2e00000-0000-4000-8000-000000000021' AND code = 'it_admin' AND description = 'Administrador Tecnico (E2E)';
UPDATE role SET description = 'Operator CDE'
 WHERE id = 'e2e00000-0000-4000-8000-000000000022' AND code = 'operator_cde' AND description = 'Operador CDE (E2E)';
UPDATE role SET description = 'Supervisor CDE / Quality'
 WHERE id = 'e2e00000-0000-4000-8000-000000000023' AND code = 'supervisor_quality' AND description = 'Supervisor de Calidad (E2E)';

UPDATE kit SET name = 'General Surgery Kit E2E'
 WHERE id = 'e2e00000-0000-4000-8000-000000000041' AND name = 'Kit Cirugia General E2E';

UPDATE procedure_kit SET technique_label = 'Open (E2E)'
 WHERE kit_id = 'e2e00000-0000-4000-8000-000000000041' AND technique_label = 'Abierta (E2E)';

UPDATE patient SET display_name = 'E2E Patient 001'
 WHERE id = 'e2e00000-0000-4000-8000-000000000051' AND display_name = 'Paciente E2E 001';

UPDATE physician SET name = 'Dr. E2E Surgeon'
 WHERE id = 'e2e00000-0000-4000-8000-000000000052' AND name = 'Dra. E2E Cirujana';

COMMIT;
