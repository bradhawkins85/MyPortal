-- Add assigned_user_id to shop_quotes table to allow assigning quotes to specific portal members
ALTER TABLE shop_quotes
  ADD COLUMN IF NOT EXISTS assigned_user_id INT NULL;

ALTER TABLE shop_quotes
  ADD INDEX IF NOT EXISTS idx_assigned_user (assigned_user_id);

ALTER TABLE shop_quotes
  ADD CONSTRAINT fk_quotes_assigned_user
  FOREIGN KEY (assigned_user_id) REFERENCES users(id)
  ON DELETE SET NULL;
