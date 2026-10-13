-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Keep each observed installation/version interval, including removed software.
CREATE TABLE IF NOT EXISTS rmm_device_software_history (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agent_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  version VARCHAR(128) NULL,
  publisher VARCHAR(255) NULL,
  install_date DATE NULL,
  source VARCHAR(32) NULL,
  first_seen_at DATETIME NOT NULL,
  last_seen_at DATETIME NOT NULL,
  removed_at DATETIME NULL,
  CONSTRAINT fk_rmm_software_history_agent FOREIGN KEY (agent_id) REFERENCES rmm_agents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_software_history_agent ON rmm_device_software_history (agent_id, removed_at);

-- Seed existing installations without inventing an install date.
INSERT INTO rmm_device_software_history
  (agent_id, name, version, publisher, install_date, source, first_seen_at, last_seen_at)
SELECT s.agent_id, s.name, s.version, s.publisher, s.install_date, s.source,
       i.received_at, i.received_at
FROM rmm_device_software s
JOIN rmm_device_inventory i ON i.agent_id = s.agent_id
WHERE i.received_at IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM rmm_device_software_history h WHERE h.agent_id = s.agent_id);
