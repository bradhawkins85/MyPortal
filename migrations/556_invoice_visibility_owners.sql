-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Track invoice creators and assignees for Mine visibility; historical invoices remain unowned.
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS created_by INT NULL;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS assigned_user_id INT NULL;
