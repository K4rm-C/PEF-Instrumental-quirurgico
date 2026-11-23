-- Align seeded free-text labels to English (Babel msgid / source language).
-- Idempotent: safe on volumes that already have Spanish kit/user display names.

BEGIN;

UPDATE kit SET name = v.en_name, active = TRUE
FROM (VALUES
  ('51515151-0000-4000-8000-000000000003'::uuid, 'Vaginal delivery kit'),
  ('51515151-0000-4000-8000-000000000004'::uuid, 'Open hernioplasty kit'),
  ('51515151-0000-4000-8000-000000000005'::uuid, 'Wound debridement and closure kit'),
  ('51515151-0000-4000-8000-000000000006'::uuid, 'Excision / minor surgery kit'),
  ('51515151-0000-4000-8000-000000000007'::uuid, 'Open appendectomy kit'),
  ('51515151-0000-4000-8000-000000000008'::uuid, 'Cesarean kit')
) AS v(id, en_name)
WHERE kit.id = v.id;

UPDATE "user" SET name = v.en_name
FROM (VALUES
  ('22222222-2222-2222-2222-222222222221'::uuid, 'Demo Operator 1'),
  ('22222222-2222-2222-2222-222222222224'::uuid, 'Demo Operator 2'),
  ('22222222-2222-2222-2222-222222222222'::uuid, 'Demo Supervisor 1'),
  ('22222222-2222-2222-2222-222222222225'::uuid, 'Demo Supervisor 2'),
  ('22222222-2222-2222-2222-222222222223'::uuid, 'Demo Admin'),
  ('22222222-2222-2222-2222-222222222226'::uuid, 'Demo Floor Supervisor'),
  ('22222222-2222-2222-2222-222222222231'::uuid, 'Operator Ana Ruiz'),
  ('22222222-2222-2222-2222-222222222232'::uuid, 'Operator Luis Pena'),
  ('22222222-2222-2222-2222-222222222233'::uuid, 'SPD Carla Soto'),
  ('22222222-2222-2222-2222-222222222234'::uuid, 'IT Admin Extra'),
  ('22222222-2222-2222-2222-222222222235'::uuid, 'Floor Nora Diaz'),
  ('22222222-2222-2222-2222-222222222236'::uuid, 'Dual Admin SPD')
) AS v(id, en_name)
WHERE "user".id = v.id;

COMMIT;
