-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 477: RMM device inventory.
--
-- rmm_device_inventory holds the latest hardware snapshot each RMM agent sent
-- (one row per agent). The columns used to pick devices for scripts, checks
-- and automations (RAM, CPU cores, disk sizes, operating system, maker and
-- model) are real columns; disks, memory modules, GPUs, network adapters and
-- collection problems are kept as JSON in details_json. refresh_requested_at
-- is set when a technician asks for a new snapshot; the agent sees it on its
-- next poll. rmm_device_software lists the programs and packages installed
-- with the latest snapshot, replaced each time one arrives.

CREATE TABLE IF NOT EXISTS rmm_device_inventory (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agent_id INT NOT NULL,
  company_id INT NULL,
  asset_id INT NULL,
  collected_at DATETIME NULL,
  received_at DATETIME NULL,
  refresh_requested_at DATETIME NULL,
  refresh_requested_by_user_id INT NULL,
  hostname VARCHAR(255) NULL,
  manufacturer VARCHAR(255) NULL,
  model VARCHAR(255) NULL,
  serial_number VARCHAR(255) NULL,
  bios_version VARCHAR(255) NULL,
  os_name VARCHAR(255) NULL,
  os_version VARCHAR(128) NULL,
  os_build VARCHAR(128) NULL,
  arch VARCHAR(32) NULL,
  domain VARCHAR(255) NULL,
  cpu_model VARCHAR(255) NULL,
  cpu_cores INT NULL,
  cpu_logical_processors INT NULL,
  ram_bytes BIGINT NULL,
  disk_total_bytes BIGINT NULL,
  system_disk_free_bytes BIGINT NULL,
  last_boot_at DATETIME NULL,
  software_count INT NOT NULL DEFAULT 0,
  details_json LONGTEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_rmm_device_inventory_agent UNIQUE (agent_id),
  CONSTRAINT fk_rmm_device_inventory_agent FOREIGN KEY (agent_id) REFERENCES rmm_agents(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_device_inventory_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_rmm_device_inventory_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_device_inventory_company ON rmm_device_inventory (company_id);
CREATE INDEX IF NOT EXISTS idx_rmm_device_inventory_asset ON rmm_device_inventory (asset_id);

CREATE TABLE IF NOT EXISTS rmm_device_software (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agent_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  version VARCHAR(128) NULL,
  publisher VARCHAR(255) NULL,
  install_date DATE NULL,
  source VARCHAR(32) NULL,
  CONSTRAINT fk_rmm_device_software_agent FOREIGN KEY (agent_id) REFERENCES rmm_agents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rmm_device_software_agent ON rmm_device_software (agent_id);
CREATE INDEX IF NOT EXISTS idx_rmm_device_software_name ON rmm_device_software (name);
