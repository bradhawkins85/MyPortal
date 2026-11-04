-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Record which customer staff member asked for an order. Technicians can place
-- an order on behalf of a staff member; the name and email are snapshotted so
-- the requester survives staff renames or removal. Older orders stay NULL.
ALTER TABLE shop_orders
  ADD COLUMN IF NOT EXISTS requested_by_staff_id INT NULL;

ALTER TABLE shop_orders
  ADD COLUMN IF NOT EXISTS requested_by_name VARCHAR(255) NULL;

ALTER TABLE shop_orders
  ADD COLUMN IF NOT EXISTS requested_by_email VARCHAR(255) NULL;
