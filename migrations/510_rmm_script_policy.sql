-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
CREATE TABLE IF NOT EXISTS rmm_script_settings (
  id INT PRIMARY KEY,
  timeout_seconds INT NOT NULL DEFAULT 600
);
INSERT INTO rmm_script_settings (id, timeout_seconds)
SELECT 1, 600 WHERE NOT EXISTS (SELECT 1 FROM rmm_script_settings WHERE id = 1);
ALTER TABLE rmm_scripts ADD COLUMN IF NOT EXISTS platforms_json TEXT NULL;
