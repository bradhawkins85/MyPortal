ALTER TABLE companies ADD COLUMN IF NOT EXISTS huntress_sat_account_id VARCHAR(64) DEFAULT NULL;
ALTER TABLE companies ADD KEY IF NOT EXISTS companies_huntress_sat_account_id (huntress_sat_account_id);
