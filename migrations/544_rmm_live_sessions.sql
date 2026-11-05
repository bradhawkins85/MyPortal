-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 544: RMM live tools (terminal, task manager, services, files,
-- registry, disk usage and real-time CPU and memory) without RustDesk or
-- MeshCentral.
--
-- rmm_live_sessions is one technician's live tools session on a device. The
-- agent sees 'requested' sessions in its job poll and connects to them over a
-- WebSocket (offered_at stops one session being offered on every poll while
-- the agent connects); the browser presents a one-time token (stored as its SHA-256
-- hash) on its own WebSocket. MyPortal relays messages between the two.
--
-- rmm_live_frames carries those messages between MyPortal workers when the
-- browser and the agent are connected to different workers and Redis is not
-- configured. Rows are deleted once read and swept after a few minutes.
--
-- rmm_agents.live_tools_supported is advertised by agents that can serve
-- live tools sessions.

CREATE TABLE IF NOT EXISTS rmm_live_sessions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  asset_id INT NOT NULL,
  agent_id INT NOT NULL,
  requested_by_user_id INT NULL,
  browser_token_hash CHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'requested',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  offered_at DATETIME NULL,
  agent_connected_at DATETIME NULL,
  browser_connected_at DATETIME NULL,
  last_activity_at DATETIME NULL,
  expires_at DATETIME NOT NULL,
  closed_at DATETIME NULL,
  close_reason VARCHAR(255) NULL,
  CONSTRAINT fk_rmm_live_sessions_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_live_sessions_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_live_sessions_agent ON rmm_live_sessions (agent_id, status);
CREATE INDEX IF NOT EXISTS idx_rmm_live_sessions_asset ON rmm_live_sessions (asset_id, created_at);

CREATE TABLE IF NOT EXISTS rmm_live_frames (
  id INT AUTO_INCREMENT PRIMARY KEY,
  channel VARCHAR(64) NOT NULL,
  payload MEDIUMTEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_live_frames_channel ON rmm_live_frames (channel, id);
CREATE INDEX IF NOT EXISTS idx_rmm_live_frames_created ON rmm_live_frames (created_at);

ALTER TABLE rmm_agents ADD COLUMN IF NOT EXISTS live_tools_supported TINYINT(1) NOT NULL DEFAULT 0;
