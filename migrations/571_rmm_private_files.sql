-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Local copy of the Private-Files repository's usable script files, refreshed
-- by Sync from Gitea so the Add file editor does not list the whole
-- repository from Gitea every time it opens.
CREATE TABLE IF NOT EXISTS rmm_private_files (
  id INT AUTO_INCREMENT PRIMARY KEY,
  path VARCHAR(1024) NOT NULL,
  sha VARCHAR(64) NOT NULL,
  size BIGINT NOT NULL DEFAULT 0,
  company_id INT NULL,
  synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
