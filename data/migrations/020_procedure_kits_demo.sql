-- Additive demo kits linked to procedures (idempotent).
-- Extends 002/003: Demo procedure owns Video Demo Kits; other SPD procedures get trays.

BEGIN;

INSERT INTO cat_procedure_type (code, name) VALUES
  ('demo',                 'Demo'),
  ('vaginal_delivery',     'Vaginal delivery'),
  ('wound_debridement',    'Surgical wound debridement and closure'),
  ('soft_tissue_excision', 'Superficial lesion excision / soft-tissue minor surgery')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

UPDATE cat_procedure_type SET name = 'Open appendectomy' WHERE code = 'appendectomy';
UPDATE cat_procedure_type SET name = 'Open hernioplasty' WHERE code = 'hernia_repair';

-- Move Video Demo Kits from lap_chole → Demo (idempotent).
DELETE FROM procedure_kit pk
USING cat_procedure_type pt
WHERE pk.procedure_type_id = pt.id
  AND pt.code = 'lap_chole'
  AND pk.kit_id IN (
    '51515151-0000-4000-8000-000000000001',
    '51515151-0000-4000-8000-000000000002'
  );

INSERT INTO procedure_kit (procedure_type_id, kit_id, technique_label, is_default, active)
SELECT pt.id, v.kit_id, v.label, v.is_default, TRUE
FROM (VALUES
  ('51515151-0000-4000-8000-000000000001'::uuid, 'Video demo', TRUE),
  ('51515151-0000-4000-8000-000000000002'::uuid, 'Compact demo', FALSE)
) AS v(kit_id, label, is_default)
JOIN cat_procedure_type pt ON pt.code = 'demo'
WHERE EXISTS (SELECT 1 FROM kit k WHERE k.id = v.kit_id)
  AND NOT EXISTS (
    SELECT 1 FROM procedure_kit pk
    WHERE pk.procedure_type_id = pt.id AND pk.kit_id = v.kit_id
  );

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
WHERE pt.code = 'demo'
  AND NOT EXISTS (
    SELECT 1 FROM procedure_phase pp
    WHERE pp.procedure_type_id = pt.id AND pp.phase_id = ph.id
  );

-- Helper: upsert kit shell (same institution as demo seed).
INSERT INTO kit (id, name, version, active, institution_id) VALUES
  ('51515151-0000-4000-8000-000000000003', 'Kit parto vaginal', 1, TRUE,
   '11111111-1111-1111-1111-111111111111'),
  ('51515151-0000-4000-8000-000000000004', 'Kit hernioplastia abierta', 1, TRUE,
   '11111111-1111-1111-1111-111111111111'),
  ('51515151-0000-4000-8000-000000000005', 'Kit desbridamiento y cierre', 1, TRUE,
   '11111111-1111-1111-1111-111111111111'),
  ('51515151-0000-4000-8000-000000000006', 'Kit exéresis / cirugía menor', 1, TRUE,
   '11111111-1111-1111-1111-111111111111'),
  ('51515151-0000-4000-8000-000000000007', 'Kit apendicectomía abierta', 1, TRUE,
   '11111111-1111-1111-1111-111111111111'),
  ('51515151-0000-4000-8000-000000000008', 'Kit cesárea', 1, TRUE,
   '11111111-1111-1111-1111-111111111111')
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, active = TRUE;

-- Replace composition for these kits (safe for demo seed).
DELETE FROM kit_item WHERE kit_id IN (
  '51515151-0000-4000-8000-000000000003',
  '51515151-0000-4000-8000-000000000004',
  '51515151-0000-4000-8000-000000000005',
  '51515151-0000-4000-8000-000000000006',
  '51515151-0000-4000-8000-000000000007',
  '51515151-0000-4000-8000-000000000008'
);

INSERT INTO kit_item (kit_id, family_id, quantity)
SELECT v.kit_id::uuid, f.id, v.qty
FROM (VALUES
  -- Parto vaginal
  ('51515151-0000-4000-8000-000000000003', 'FOERSTER', 1),
  ('51515151-0000-4000-8000-000000000003', 'KELLY_FORCEPS', 1),
  ('51515151-0000-4000-8000-000000000003', 'ROCHESTER', 1),
  ('51515151-0000-4000-8000-000000000003', 'ADSON_PLAIN', 1),
  ('51515151-0000-4000-8000-000000000003', 'ADSON_TEETH', 1),
  ('51515151-0000-4000-8000-000000000003', 'MAYO_STRAIGHT', 1),
  ('51515151-0000-4000-8000-000000000003', 'MAYO_CURVED', 1),
  ('51515151-0000-4000-8000-000000000003', 'MAYOHEG', 1),
  ('51515151-0000-4000-8000-000000000003', 'OLSENHEG', 1),
  ('51515151-0000-4000-8000-000000000003', 'SCALPEL3', 1),
  ('51515151-0000-4000-8000-000000000003', 'FARABEUF', 2),
  -- Hernioplastia abierta
  ('51515151-0000-4000-8000-000000000004', 'SCALPEL3', 1),
  ('51515151-0000-4000-8000-000000000004', 'SCALPEL4', 1),
  ('51515151-0000-4000-8000-000000000004', 'ADSON_PLAIN', 1),
  ('51515151-0000-4000-8000-000000000004', 'ADSON_TEETH', 1),
  ('51515151-0000-4000-8000-000000000004', 'ALLIS', 1),
  ('51515151-0000-4000-8000-000000000004', 'KELLY_FORCEPS', 1),
  ('51515151-0000-4000-8000-000000000004', 'MOSQUITO', 1),
  ('51515151-0000-4000-8000-000000000004', 'MAYO_STRAIGHT', 1),
  ('51515151-0000-4000-8000-000000000004', 'MAYO_CURVED', 1),
  ('51515151-0000-4000-8000-000000000004', 'MAYOHEG', 1),
  ('51515151-0000-4000-8000-000000000004', 'OLSENHEG', 1),
  ('51515151-0000-4000-8000-000000000004', 'FARABEUF', 2),
  -- Desbridamiento y cierre
  ('51515151-0000-4000-8000-000000000005', 'SCALPEL3', 1),
  ('51515151-0000-4000-8000-000000000005', 'ADSON_PLAIN', 1),
  ('51515151-0000-4000-8000-000000000005', 'ADSON_TEETH', 1),
  ('51515151-0000-4000-8000-000000000005', 'ALLIS', 1),
  ('51515151-0000-4000-8000-000000000005', 'KELLY_FORCEPS', 1),
  ('51515151-0000-4000-8000-000000000005', 'MOSQUITO', 1),
  ('51515151-0000-4000-8000-000000000005', 'MAYO_STRAIGHT', 1),
  ('51515151-0000-4000-8000-000000000005', 'MAYO_CURVED', 1),
  ('51515151-0000-4000-8000-000000000005', 'MAYOHEG', 1),
  ('51515151-0000-4000-8000-000000000005', 'OLSENHEG', 1),
  ('51515151-0000-4000-8000-000000000005', 'FARABEUF', 2),
  -- Exéresis / cirugía menor
  ('51515151-0000-4000-8000-000000000006', 'SCALPEL3', 1),
  ('51515151-0000-4000-8000-000000000006', 'ADSON_PLAIN', 1),
  ('51515151-0000-4000-8000-000000000006', 'ADSON_TEETH', 1),
  ('51515151-0000-4000-8000-000000000006', 'KELLY_FORCEPS', 1),
  ('51515151-0000-4000-8000-000000000006', 'MOSQUITO', 1),
  ('51515151-0000-4000-8000-000000000006', 'ALLIS', 1),
  ('51515151-0000-4000-8000-000000000006', 'MAYO_STRAIGHT', 1),
  ('51515151-0000-4000-8000-000000000006', 'MAYO_CURVED', 1),
  ('51515151-0000-4000-8000-000000000006', 'MAYOHEG', 1),
  ('51515151-0000-4000-8000-000000000006', 'FARABEUF', 2),
  -- Apendicectomía abierta
  ('51515151-0000-4000-8000-000000000007', 'SCALPEL3', 1),
  ('51515151-0000-4000-8000-000000000007', 'KELLY_FORCEPS', 1),
  ('51515151-0000-4000-8000-000000000007', 'MOSQUITO', 1),
  ('51515151-0000-4000-8000-000000000007', 'ALLIS', 1),
  ('51515151-0000-4000-8000-000000000007', 'ADSON_PLAIN', 1),
  ('51515151-0000-4000-8000-000000000007', 'ADSON_TEETH', 1),
  ('51515151-0000-4000-8000-000000000007', 'MAYO_STRAIGHT', 1),
  ('51515151-0000-4000-8000-000000000007', 'MAYO_CURVED', 1),
  ('51515151-0000-4000-8000-000000000007', 'MAYOHEG', 1),
  ('51515151-0000-4000-8000-000000000007', 'FARABEUF', 2),
  ('51515151-0000-4000-8000-000000000007', 'PROBE', 1),
  ('51515151-0000-4000-8000-000000000007', 'FOERSTER', 1),
  ('51515151-0000-4000-8000-000000000007', 'ROCHESTER', 1),
  -- Cesárea
  ('51515151-0000-4000-8000-000000000008', 'SCALPEL3', 1),
  ('51515151-0000-4000-8000-000000000008', 'KELLY_FORCEPS', 1),
  ('51515151-0000-4000-8000-000000000008', 'ROCHESTER', 1),
  ('51515151-0000-4000-8000-000000000008', 'ALLIS', 1),
  ('51515151-0000-4000-8000-000000000008', 'FOERSTER', 1),
  ('51515151-0000-4000-8000-000000000008', 'ADSON_PLAIN', 1),
  ('51515151-0000-4000-8000-000000000008', 'ADSON_TEETH', 1),
  ('51515151-0000-4000-8000-000000000008', 'MAYO_STRAIGHT', 1),
  ('51515151-0000-4000-8000-000000000008', 'MAYO_CURVED', 1),
  ('51515151-0000-4000-8000-000000000008', 'MAYOHEG', 1),
  ('51515151-0000-4000-8000-000000000008', 'FARABEUF', 2)
) AS v(kit_id, family_code, qty)
JOIN instrument_family f ON f.code = v.family_code;

-- Link kits to procedures (default = true for each).
INSERT INTO procedure_kit (procedure_type_id, kit_id, technique_label, is_default, active)
SELECT pt.id, v.kit_id, v.label, TRUE, TRUE
FROM (VALUES
  ('vaginal_delivery',     '51515151-0000-4000-8000-000000000003'::uuid, 'Standard'),
  ('hernia_repair',        '51515151-0000-4000-8000-000000000004'::uuid, 'Open'),
  ('wound_debridement',    '51515151-0000-4000-8000-000000000005'::uuid, 'Standard'),
  ('soft_tissue_excision', '51515151-0000-4000-8000-000000000006'::uuid, 'Standard'),
  ('appendectomy',         '51515151-0000-4000-8000-000000000007'::uuid, 'Open'),
  ('cesarean',             '51515151-0000-4000-8000-000000000008'::uuid, 'Standard')
) AS v(proc_code, kit_id, label)
JOIN cat_procedure_type pt ON pt.code = v.proc_code
WHERE NOT EXISTS (
  SELECT 1 FROM procedure_kit pk
  WHERE pk.procedure_type_id = pt.id AND pk.kit_id = v.kit_id
);

-- Ensure one default per procedure among the kits we just linked.
UPDATE procedure_kit pk
SET is_default = TRUE, active = TRUE
FROM cat_procedure_type pt
WHERE pk.procedure_type_id = pt.id
  AND pt.code IN (
    'vaginal_delivery', 'hernia_repair', 'wound_debridement',
    'soft_tissue_excision', 'appendectomy', 'cesarean'
  )
  AND pk.kit_id IN (
    '51515151-0000-4000-8000-000000000003',
    '51515151-0000-4000-8000-000000000004',
    '51515151-0000-4000-8000-000000000005',
    '51515151-0000-4000-8000-000000000006',
    '51515151-0000-4000-8000-000000000007',
    '51515151-0000-4000-8000-000000000008'
  );

COMMIT;
