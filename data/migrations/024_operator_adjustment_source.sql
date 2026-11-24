-- Allow expected_inventory.source = operator_adjustment (OP pre-start report differences).
ALTER TABLE expected_inventory
  DROP CONSTRAINT IF EXISTS chk_expected_inventory_source;

ALTER TABLE expected_inventory
  ADD CONSTRAINT chk_expected_inventory_source CHECK (
    source IN (
      'kit_snapshot',
      'schedule_additional',
      'live_add',
      'manual',
      'operator_adjustment'
    )
  );
