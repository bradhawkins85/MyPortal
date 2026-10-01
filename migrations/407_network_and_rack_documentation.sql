-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

CREATE TABLE IF NOT EXISTS ip_networks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    name VARCHAR(191) NOT NULL,
    cidr VARCHAR(49) NOT NULL,
    description VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ip_network_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT uq_ip_network_cidr UNIQUE (company_id, cidr)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ip_addresses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    network_id INT NOT NULL,
    address VARCHAR(45) NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'reserved',
    asset_id INT NULL,
    notes VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ip_address_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_ip_address_network FOREIGN KEY (network_id) REFERENCES ip_networks(id) ON DELETE CASCADE,
    CONSTRAINT fk_ip_address_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL,
    CONSTRAINT uq_ip_address_company UNIQUE (company_id, address)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ip_dns_names (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ip_address_id INT NOT NULL,
    name VARCHAR(253) NOT NULL,
    CONSTRAINT fk_ip_dns_address FOREIGN KEY (ip_address_id) REFERENCES ip_addresses(id) ON DELETE CASCADE,
    CONSTRAINT uq_ip_dns_name UNIQUE (ip_address_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS racks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    name VARCHAR(191) NOT NULL,
    location VARCHAR(255) NULL,
    unit_count INT NOT NULL DEFAULT 42,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rack_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT uq_rack_name UNIQUE (company_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rack_equipment (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    rack_id INT NOT NULL,
    asset_id INT NOT NULL,
    start_unit INT NOT NULL,
    unit_height INT NOT NULL DEFAULT 1,
    face VARCHAR(8) NOT NULL DEFAULT 'front',
    notes VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rack_equipment_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_rack_equipment_rack FOREIGN KEY (rack_id) REFERENCES racks(id) ON DELETE CASCADE,
    CONSTRAINT fk_rack_equipment_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
    CONSTRAINT uq_rack_asset UNIQUE (company_id, asset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rack_equipment_units (
    equipment_id INT NOT NULL,
    rack_id INT NOT NULL,
    unit_number INT NOT NULL,
    face VARCHAR(8) NOT NULL,
    PRIMARY KEY (rack_id, unit_number, face),
    CONSTRAINT fk_rack_unit_equipment FOREIGN KEY (equipment_id) REFERENCES rack_equipment(id) ON DELETE CASCADE,
    CONSTRAINT fk_rack_unit_rack FOREIGN KEY (rack_id) REFERENCES racks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
