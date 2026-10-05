-- =============================================================================
-- PEF · 002_seed_catalogs.sql
-- Catalogos de valores cerrados + version inicial del aviso de privacidad.
--
-- AMBITO: todos los ambientes, incluido produccion. Es requisito de arranque:
-- sin estos registros el sistema no puede crear una operacion ni una sesion.
--
-- IDEMPOTENTE: usa ON CONFLICT (code) DO UPDATE, de modo que puede volver a
-- ejecutarse tras agregar valores nuevos sin duplicar ni romper referencias.
-- Los identificadores se generan por omision; las referencias entre tablas
-- se resuelven siempre por `code`, nunca por UUID literal.
--
-- IDIOMA: el campo `code` es el identificador estable en ingles y es el valor
-- que viaja a MongoDB y a las integraciones. El campo `name` es texto de
-- despliegue y puede traducirse sin afectar la integridad de los datos.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Demografia minima del paciente
-- -----------------------------------------------------------------------------
INSERT INTO cat_gender (code, name) VALUES
  ('female',  'Femenino'),
  ('male',    'Masculino'),
  ('other',   'Otro'),
  ('unknown', 'No especificado')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Especialidad medica
-- -----------------------------------------------------------------------------
INSERT INTO cat_specialty (code, name) VALUES
  ('general_surgery',  'Cirugia general'),
  ('orthopedics',      'Traumatologia y ortopedia'),
  ('gynecology',       'Ginecologia y obstetricia'),
  ('urology',          'Urologia'),
  ('neurosurgery',     'Neurocirugia'),
  ('anesthesiology',   'Anestesiologia')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Tipo de procedimiento quirurgico
-- Catalogo definido por la institucion. Los valores aqui son un punto de
-- partida representativo y se amplian segun el hospital.
-- -----------------------------------------------------------------------------
INSERT INTO cat_procedure_type (code, name) VALUES
  ('lap_chole',      'Colecistectomia laparoscopica'),
  ('open_chole',     'Colecistectomia abierta'),
  ('appendectomy',   'Apendicectomia'),
  ('hernia_repair',  'Plastia inguinal'),
  ('cesarean',       'Cesarea'),
  ('hip_replace',    'Artroplastia de cadera')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Rol del medico en el acto quirurgico
-- -----------------------------------------------------------------------------
INSERT INTO cat_surgical_role (code, name) VALUES
  ('surgeon',         'Cirujano'),
  ('first_assistant', 'Primer ayudante'),
  ('anesthesiologist','Anestesiologo'),
  ('resident',        'Medico residente')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Estado de la operacion
-- -----------------------------------------------------------------------------
INSERT INTO cat_operation_status (code, name) VALUES
  ('scheduled',   'Programada'),
  ('in_progress', 'En curso'),
  ('closed',      'Cerrada'),
  ('cancelled',   'Cancelada')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Estado de la sesion de conteo (maquina de estados RF)
--   scheduled -> in_progress -> awaiting_spd_review|correction_required -> closed
--   cualquier estado elegible -> aborted
-- -----------------------------------------------------------------------------
INSERT INTO cat_session_status (code, name) VALUES
  ('scheduled',           'Programada'),
  ('in_progress',         'En progreso'),
  ('awaiting_spd_review', 'En espera de revision SPD'),
  ('correction_required', 'Correccion requerida'),
  ('closed',              'Cerrada'),
  ('aborted',             'Abortada')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Ciclo de vida de la pieza de instrumental
-- -----------------------------------------------------------------------------
INSERT INTO cat_instrument_cycle_status (code, name) VALUES
  ('available',     'Available'),
  ('reserved',      'Reserved'),
  ('in_use',        'In use'),
  ('sterilization', 'Sterilization'),
  ('maintenance',   'Maintenance'),
  ('retired',       'Retired'),
  ('lost',          'Lost / not recovered')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Categoria funcional de la familia de instrumental (UI Type filter / kit blocks)
-- Order used in composition: Cutting → Dissection → Grasping → Hemostasis →
-- Retraction → Suction → Suturing
-- -----------------------------------------------------------------------------
INSERT INTO cat_instrument_category (code, name) VALUES
  ('cutting',    'Cutting'),
  ('dissection', 'Dissection'),
  ('grasping',   'Grasping'),
  ('hemostasis', 'Hemostasis'),
  ('retraction', 'Retraction'),
  ('suction',    'Suction'),
  ('suturing',   'Suturing')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Contexto de uso del instrumental
-- -----------------------------------------------------------------------------
INSERT INTO cat_usage_context (code, name) VALUES
  ('operative',   'Uso operativo en acto quirurgico'),
  ('pedagogical', 'Uso pedagogico o de entrenamiento')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Tipificacion de la discrepancia
-- -----------------------------------------------------------------------------
INSERT INTO cat_discrepancy_reason (code, name) VALUES
  ('shortage',        'Faltante respecto al inventario esperado'),
  ('surplus',         'Sobrante respecto al inventario esperado'),
  ('unidentified',    'Pieza presente no identificada por el modelo'),
  ('occluded',        'Pieza ocluida o fuera de la region de interes'),
  ('misclassified',   'Pieza clasificada en familia incorrecta'),
  ('operator_error',  'Error de captura o manipulacion del operador')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Motivo de captura de evidencia visual
-- Estos codigos son los valores admitidos del campo `reason` en la coleccion
-- checkpoint_frame de MongoDB. Ver PEF-DS06.
-- -----------------------------------------------------------------------------
INSERT INTO cat_checkpoint_reason (code, name) VALUES
  ('start',        'Inicio de sesion'),
  ('close',        'Cierre de sesion'),
  ('hourly',       'Captura periodica'),
  ('state_change', 'Cambio de estado relevante'),
  ('discrepancy',  'Discrepancia registrada'),
  ('manual_pin',   'Marca manual del operador')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Fase quirurgica / de conteo
-- Estos codigos son los valores admitidos del campo `phase_code` en la
-- coleccion checkpoint_frame de MongoDB.
--
-- ADVERTENCIA: esta lista requiere validacion clinica antes de produccion.
-- La secuencia aplicable a cada procedimiento se configura en procedure_phase,
-- no aqui: este catalogo solo declara que fases existen.
-- -----------------------------------------------------------------------------
INSERT INTO cat_operation_phase (code, name, active) VALUES
  ('start',        'Start / initial stage', TRUE),
  ('demo_phase_1', 'Demo phase one',        TRUE),
  ('demo_phase_2', 'Demo phase two',        TRUE),
  ('demo_phase_3', 'Demo phase three',      TRUE),
  ('final_count',  'Final count',           TRUE)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Vocabulario de eventos de conteo
-- Es el dominio de count_event.event_type_id.
-- -----------------------------------------------------------------------------
INSERT INTO cat_event_type (code, name) VALUES
  ('session_open',       'Apertura de sesion'),
  ('auto_count',         'Conteo sugerido por el modelo'),
  ('manual_count',       'Conteo capturado manualmente'),
  ('manual_close',       'Cierre con reporte manual de cantidades'),
  ('phase_change',       'Cambio de fase quirurgica'),
  ('discrepancy_raised', 'Discrepancia detectada'),
  ('correction_applied', 'Correccion humana aplicada'),
  ('validation_passed',  'Validacion sin discrepancias'),
  ('close_blocked',      'Intento de cierre bloqueado'),
  ('session_close',      'Cierre de sesion')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Finalidades de tratamiento de datos personales (LFPDPPP / GDPR)
-- Los tres codigos estan fijados por CHECK en el esquema; no se pueden
-- agregar finalidades nuevas sin migracion.
-- -----------------------------------------------------------------------------
INSERT INTO cat_processing_purpose (code, name, active) VALUES
  ('quality_ops',       'Calidad y operacion del proceso de conteo', TRUE),
  ('model_improvement', 'Mejora del modelo de vision',               TRUE),
  ('external_sharing',  'Transferencia a terceros',                  TRUE)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

-- -----------------------------------------------------------------------------
-- Version inicial del aviso de privacidad (PLACEHOLDER demo)
--
-- document_uri / content_sha256 NO son un PDF legal real. Sirven para ejercitar
-- la usabilidad RF (confirmar aviso, finalidades quality_ops / model_improvement,
-- o sesion sin aviso → manual_no_privacy). Sustituir antes de datos reales.
-- -----------------------------------------------------------------------------
INSERT INTO privacy_notice_version (version, effective_at, document_uri, content_sha256, active)
VALUES (
  'v1.0',
  '2026-09-01T00:00:00Z',
  'placeholder://privacy-notice/v1.0',
  repeat('0', 64),
  TRUE
)
ON CONFLICT (version) DO NOTHING;

COMMIT;

-- -----------------------------------------------------------------------------
-- Verificacion posterior a la carga
-- -----------------------------------------------------------------------------
-- SELECT 'cat_gender' AS catalogo, count(*) FROM cat_gender
-- UNION ALL SELECT 'cat_specialty', count(*) FROM cat_specialty
-- UNION ALL SELECT 'cat_procedure_type', count(*) FROM cat_procedure_type
-- UNION ALL SELECT 'cat_surgical_role', count(*) FROM cat_surgical_role
-- UNION ALL SELECT 'cat_operation_status', count(*) FROM cat_operation_status
-- UNION ALL SELECT 'cat_session_status', count(*) FROM cat_session_status
-- UNION ALL SELECT 'cat_instrument_cycle_status', count(*) FROM cat_instrument_cycle_status
-- UNION ALL SELECT 'cat_instrument_category', count(*) FROM cat_instrument_category
-- UNION ALL SELECT 'cat_usage_context', count(*) FROM cat_usage_context
-- UNION ALL SELECT 'cat_discrepancy_reason', count(*) FROM cat_discrepancy_reason
-- UNION ALL SELECT 'cat_checkpoint_reason', count(*) FROM cat_checkpoint_reason
-- UNION ALL SELECT 'cat_operation_phase', count(*) FROM cat_operation_phase
-- UNION ALL SELECT 'cat_event_type', count(*) FROM cat_event_type
-- UNION ALL SELECT 'cat_processing_purpose', count(*) FROM cat_processing_purpose
-- ORDER BY 1;
