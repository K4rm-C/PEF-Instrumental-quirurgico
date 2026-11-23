-- Vision-ready scheduled sessions per demo/SPD kit (idempotent).
-- Aligns WS-C1000002 with Demo procedure + Video Demo Kit 1.
-- Adds companion Overview + parto / hernioplastia / cierre / exéresis sessions
-- for Demo Operator 1 (operator@instrumed.com) with SPD privacy agreement.

BEGIN;

-- Ensure Demo Kit 1 composition matches the vision tray (idempotent replace).
DELETE FROM kit_item WHERE kit_id = '51515151-0000-4000-8000-000000000001';
INSERT INTO kit_item (kit_id, family_id, quantity)
SELECT '51515151-0000-4000-8000-000000000001', f.id, v.qty
FROM (VALUES
  ('POZZI', 1), ('SCALPEL3', 1), ('MAYO_CURVED', 1), ('MAYO_STRAIGHT', 1),
  ('KELLY_FORCEPS', 1), ('ALLIS', 1), ('FOERSTER', 1), ('ROCHESTER', 1),
  ('OLSENHEG', 1), ('MAYOHEG', 1), ('FARABEUF', 2), ('ADSON_PLAIN', 1), ('ADSON_TEETH', 1)
) AS v(family_code, qty)
JOIN instrument_family f ON f.code = v.family_code;

UPDATE kit
SET name = 'Video Demo Overview', active = TRUE
WHERE id = '51515151-0000-4000-8000-000000000002';

UPDATE procedure_kit pk
SET technique_label = 'Overview', active = TRUE
FROM cat_procedure_type pt
WHERE pk.procedure_type_id = pt.id
  AND pt.code = 'demo'
  AND pk.kit_id = '51515151-0000-4000-8000-000000000002';

-- WS-C1000002 / WS-C1000003 operations → Demo procedure (kit already Demo Kit 1).
UPDATE operation o
SET procedure_type_id = pt.id
FROM cat_procedure_type pt
WHERE o.id IN (
  '61616161-0000-4000-8000-000000000002',
  '61616161-0000-4000-8000-000000000003'
)
AND pt.code = 'demo';

-- Refresh kit_snapshot inventory for scheduled Demo Kit 1 sessions.
DELETE FROM expected_inventory
WHERE session_id IN (
  'c1000002-0000-4000-8000-000000000001',
  'c1000003-0000-4000-8000-000000000001'
)
AND source = 'kit_snapshot';

INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source)
SELECT s.session_id::uuid, ki.family_id, ki.quantity, 'kit_snapshot'
FROM (VALUES
  ('c1000002-0000-4000-8000-000000000001'),
  ('c1000003-0000-4000-8000-000000000001')
) AS s(session_id)
JOIN work_session ws ON ws.id = s.session_id::uuid
JOIN kit_item ki ON ki.kit_id = ws.kit_id;

-- Phases for SPD procedures (same demo sequence used by vision demos).
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
WHERE pt.code IN (
  'demo', 'vaginal_delivery', 'hernia_repair',
  'wound_debridement', 'soft_tissue_excision', 'appendectomy', 'cesarean'
)
  AND NOT EXISTS (
    SELECT 1 FROM procedure_phase pp
    WHERE pp.procedure_type_id = pt.id AND pp.phase_id = ph.id
  );

-- Operations for vision-ready trays.
INSERT INTO operation (id, scheduled_at, started_at, ended_at, status_id, procedure_type_id, room_id, institution_id)
SELECT v.op_id::uuid,
       (date_trunc('day', now() AT TIME ZONE 'UTC') + v.hour_offset),
       NULL, NULL, st.id, pt.id,
       '12121212-0000-4000-8000-000000000001',
       '11111111-1111-1111-1111-111111111111'
FROM (VALUES
  ('61616161-0000-4000-8000-000000000021', 'demo',                 interval '11 hours'),
  ('61616161-0000-4000-8000-000000000022', 'vaginal_delivery',     interval '12 hours'),
  ('61616161-0000-4000-8000-000000000023', 'hernia_repair',        interval '13 hours'),
  ('61616161-0000-4000-8000-000000000024', 'wound_debridement',    interval '14 hours'),
  ('61616161-0000-4000-8000-000000000025', 'soft_tissue_excision', interval '16 hours')
) AS v(op_id, proc_code, hour_offset)
JOIN cat_operation_status st ON st.code = 'scheduled'
JOIN cat_procedure_type pt ON pt.code = v.proc_code
WHERE NOT EXISTS (SELECT 1 FROM operation o WHERE o.id = v.op_id::uuid);

INSERT INTO operation_patient (operation_id, patient_id)
SELECT v.op_id::uuid, '31313131-0000-4000-8000-000000000001'
FROM (VALUES
  ('61616161-0000-4000-8000-000000000021'),
  ('61616161-0000-4000-8000-000000000022'),
  ('61616161-0000-4000-8000-000000000023'),
  ('61616161-0000-4000-8000-000000000024'),
  ('61616161-0000-4000-8000-000000000025')
) AS v(op_id)
WHERE EXISTS (SELECT 1 FROM operation o WHERE o.id = v.op_id::uuid)
  AND NOT EXISTS (
    SELECT 1 FROM operation_patient op WHERE op.operation_id = v.op_id::uuid
  );

INSERT INTO operation_physician (operation_id, physician_id, surgical_role_id)
SELECT v.op_id::uuid, '32323232-0000-4000-8000-000000000001', r.id
FROM (VALUES
  ('61616161-0000-4000-8000-000000000021'),
  ('61616161-0000-4000-8000-000000000022'),
  ('61616161-0000-4000-8000-000000000023'),
  ('61616161-0000-4000-8000-000000000024'),
  ('61616161-0000-4000-8000-000000000025')
) AS v(op_id)
JOIN cat_surgical_role r ON r.code = 'surgeon'
WHERE EXISTS (SELECT 1 FROM operation o WHERE o.id = v.op_id::uuid)
  AND NOT EXISTS (
    SELECT 1 FROM operation_physician op WHERE op.operation_id = v.op_id::uuid
  );

INSERT INTO work_session (
  id, started_at, ended_at, status_id, user_id, closed_by_user_id,
  operation_id, station_id, kit_id, current_phase_id, phase_changed_at,
  capture_mode, atypical_session, extended_retention, retention_until)
SELECT v.session_id::uuid, NULL, NULL, ss.id,
       '22222222-2222-2222-2222-222222222221',
       NULL,
       v.op_id::uuid,
       '13131313-0000-4000-8000-000000000001',
       v.kit_id::uuid,
       ph.id, NULL,
       NULL, FALSE, FALSE, NULL
FROM (VALUES
  ('c1000021-0000-4000-8000-000000000001', '61616161-0000-4000-8000-000000000021',
   '51515151-0000-4000-8000-000000000002'),
  ('c1000022-0000-4000-8000-000000000001', '61616161-0000-4000-8000-000000000022',
   '51515151-0000-4000-8000-000000000003'),
  ('c1000023-0000-4000-8000-000000000001', '61616161-0000-4000-8000-000000000023',
   '51515151-0000-4000-8000-000000000004'),
  ('c1000024-0000-4000-8000-000000000001', '61616161-0000-4000-8000-000000000024',
   '51515151-0000-4000-8000-000000000005'),
  ('c1000025-0000-4000-8000-000000000001', '61616161-0000-4000-8000-000000000025',
   '51515151-0000-4000-8000-000000000006')
) AS v(session_id, op_id, kit_id)
JOIN cat_session_status ss ON ss.code = 'scheduled'
JOIN cat_operation_phase ph ON ph.code = 'start'
WHERE EXISTS (SELECT 1 FROM operation o WHERE o.id = v.op_id::uuid)
  AND EXISTS (SELECT 1 FROM kit k WHERE k.id = v.kit_id::uuid)
  AND NOT EXISTS (SELECT 1 FROM work_session ws WHERE ws.id = v.session_id::uuid);

INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source)
SELECT v.session_id::uuid, ki.family_id, ki.quantity, 'kit_snapshot'
FROM (VALUES
  ('c1000021-0000-4000-8000-000000000001', '51515151-0000-4000-8000-000000000002'),
  ('c1000022-0000-4000-8000-000000000001', '51515151-0000-4000-8000-000000000003'),
  ('c1000023-0000-4000-8000-000000000001', '51515151-0000-4000-8000-000000000004'),
  ('c1000024-0000-4000-8000-000000000001', '51515151-0000-4000-8000-000000000005'),
  ('c1000025-0000-4000-8000-000000000001', '51515151-0000-4000-8000-000000000006')
) AS v(session_id, kit_id)
JOIN kit_item ki ON ki.kit_id = v.kit_id::uuid
WHERE EXISTS (SELECT 1 FROM work_session ws WHERE ws.id = v.session_id::uuid)
  AND NOT EXISTS (
    SELECT 1 FROM expected_inventory ei
    WHERE ei.session_id = v.session_id::uuid AND ei.family_id = ki.family_id
  );

INSERT INTO session_processing_agreement (
  session_id, privacy_notice_version_id, purpose_quality_ops,
  purpose_model_improvement, agreed_at, agreed_by_user_id)
SELECT v.session_id::uuid, pnv.id, TRUE, FALSE,
       (now() AT TIME ZONE 'UTC') - interval '1 hour',
       '22222222-2222-2222-2222-222222222222'
FROM (VALUES
  ('c1000021-0000-4000-8000-000000000001'),
  ('c1000022-0000-4000-8000-000000000001'),
  ('c1000023-0000-4000-8000-000000000001'),
  ('c1000024-0000-4000-8000-000000000001'),
  ('c1000025-0000-4000-8000-000000000001')
) AS v(session_id)
CROSS JOIN privacy_notice_version pnv
WHERE pnv.version = 'v1.0'
  AND EXISTS (SELECT 1 FROM work_session ws WHERE ws.id = v.session_id::uuid)
  AND NOT EXISTS (
    SELECT 1 FROM session_processing_agreement spa
    WHERE spa.session_id = v.session_id::uuid
  );

COMMIT;
