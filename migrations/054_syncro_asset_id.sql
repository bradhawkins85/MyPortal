ALTER TABLE assets
  ADD COLUMN syncro_asset_id VARCHAR(255) DEFAULT NULL,
  ADD UNIQUE KEY assets_company_syncro_id (company_id, syncro_asset_id);
