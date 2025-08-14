ALTER TABLE form_permissions ADD COLUMN company_id INT NOT NULL DEFAULT 0 AFTER user_id;
ALTER TABLE form_permissions DROP PRIMARY KEY,
  ADD PRIMARY KEY (form_id, user_id, company_id);
ALTER TABLE form_permissions ADD CONSTRAINT fk_form_permissions_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE;
