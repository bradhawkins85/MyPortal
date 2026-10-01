-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Allow users without a primary company. Deleting a company already clears
-- users.company_id, which failed while the column was NOT NULL (for example
-- removing the Demo Company that the first super admin is attached to).
ALTER TABLE users MODIFY company_id INT NULL;
