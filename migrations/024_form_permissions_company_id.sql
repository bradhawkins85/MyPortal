-- Allow NULL initially so existing rows can be backfilled
ALTER TABLE form_permissions ADD COLUMN company_id INT AFTER user_id;

-- Populate the new column using each user's primary company
UPDATE form_permissions fp
JOIN users u ON fp.user_id = u.id
SET fp.company_id = u.company_id
WHERE fp.company_id IS NULL;

-- Enforce the new constraints and key structure
ALTER TABLE form_permissions
  MODIFY company_id INT NOT NULL,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (form_id, user_id, company_id);

ALTER TABLE form_permissions
  ADD CONSTRAINT fk_form_permissions_company
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE;
