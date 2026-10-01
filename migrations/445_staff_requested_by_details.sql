-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Snapshot the name and email of the MyPortal user who submitted a staff
-- request so it survives user renames/deletion and can be passed to external
-- workflow scripts. Older rows fall back to requested_by_user_id at runtime.
ALTER TABLE staff_requests
  ADD COLUMN IF NOT EXISTS requested_by_name VARCHAR(255) NULL AFTER requested_by_user_id;

ALTER TABLE staff_requests
  ADD COLUMN IF NOT EXISTS requested_by_email VARCHAR(255) NULL AFTER requested_by_name;

ALTER TABLE staff
  ADD COLUMN IF NOT EXISTS requested_by_name VARCHAR(255) NULL AFTER requested_by_user_id;

ALTER TABLE staff
  ADD COLUMN IF NOT EXISTS requested_by_email VARCHAR(255) NULL AFTER requested_by_name;
