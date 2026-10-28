-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 531: remember each asset's MeshCentral node id.
--
-- Once a MeshCentral activation finds the device's node, its id is kept here
-- so later MeshCentral actions open straight to the device
-- (/?viewmode=10&gotonode=...) while the activation script runs, instead of a
-- MeshCentral list filtered to the asset's name.

CREATE TABLE IF NOT EXISTS rmm_meshcentral_nodes (
  asset_id INT NOT NULL PRIMARY KEY,
  node_id VARCHAR(256) NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_rmm_meshcentral_nodes_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
