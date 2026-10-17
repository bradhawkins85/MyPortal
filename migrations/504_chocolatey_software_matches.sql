-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Preserve the exact registry name separately from the Chocolatey package title.
CREATE TABLE IF NOT EXISTS rmm_chocolatey_software_matches (
  name VARCHAR(255) NOT NULL PRIMARY KEY,
  package_id VARCHAR(128) NOT NULL,
  CONSTRAINT fk_chocolatey_software_match_package FOREIGN KEY (package_id)
    REFERENCES rmm_chocolatey_packages(package_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
