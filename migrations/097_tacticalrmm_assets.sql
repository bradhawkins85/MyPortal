ALTER TABLE companies
  ADD COLUMN tacticalrmm_client_id VARCHAR(255) DEFAULT NULL,
  ADD KEY companies_tacticalrmm_client_id (tacticalrmm_client_id);

ALTER TABLE assets
  ADD COLUMN tactical_asset_id VARCHAR(255) DEFAULT NULL,
  ADD UNIQUE KEY assets_company_tactical_id (company_id, tactical_asset_id);

CREATE TABLE IF NOT EXISTS ticket_assets (
  id INT AUTO_INCREMENT PRIMARY KEY,
  ticket_id INT NOT NULL,
  asset_id INT NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY ticket_asset_unique (ticket_id, asset_id),
  CONSTRAINT fk_ticket_assets_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
  CONSTRAINT fk_ticket_assets_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);

INSERT INTO scheduled_tasks (name, command, cron, active)
SELECT 'Sync Tactical RMM assets', 'sync_tactical_assets', '0 * * * *', 0
WHERE NOT EXISTS (
  SELECT 1 FROM scheduled_tasks WHERE command = 'sync_tactical_assets'
);
