-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 469: Tags blocked on one asset.
--
-- A row stops a tag reaching that asset: the automatic rules do not add it
-- (a desktop acting as a server can be kept out of "Workstation"), and the
-- tag on the asset's company no longer counts for the asset in script and
-- automation filters. Unblocking deletes the row and the rules apply again.

CREATE TABLE IF NOT EXISTS asset_tag_blocks (
  asset_id INT NOT NULL,
  tag_id INT NOT NULL,
  created_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (asset_id, tag_id),
  CONSTRAINT fk_asset_tag_blocks_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
  CONSTRAINT fk_asset_tag_blocks_tag FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_asset_tag_blocks_tag ON asset_tag_blocks (tag_id);
