ALTER TABLE companies ADD COLUMN IF NOT EXISTS huntress_organization_id VARCHAR(64) DEFAULT NULL;
ALTER TABLE companies ADD KEY IF NOT EXISTS companies_huntress_organization_id (huntress_organization_id);
