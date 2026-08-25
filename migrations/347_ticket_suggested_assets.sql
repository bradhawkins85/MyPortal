CREATE TABLE IF NOT EXISTS ticket_suggested_assets (
  ticket_id INT NOT NULL,
  asset_id INT NOT NULL,
  matched_username VARCHAR(255) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (ticket_id, asset_id),
  CONSTRAINT ticket_suggested_assets_ticket_fk FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
  CONSTRAINT ticket_suggested_assets_asset_fk FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);
