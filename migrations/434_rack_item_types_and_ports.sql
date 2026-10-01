-- Migration 434: Standalone rack devices, patch panels, switches, and asset-linked ports.
ALTER TABLE rack_equipment DROP FOREIGN KEY fk_rack_equipment_asset;
ALTER TABLE rack_equipment MODIFY COLUMN asset_id INT NULL;
ALTER TABLE rack_equipment ADD COLUMN item_type VARCHAR(24) NOT NULL DEFAULT 'device';
ALTER TABLE rack_equipment ADD COLUMN name VARCHAR(191) NULL;
ALTER TABLE rack_equipment ADD COLUMN port_count INT NOT NULL DEFAULT 0;
ALTER TABLE rack_equipment ADD CONSTRAINT fk_rack_equipment_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS rack_equipment_ports (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    equipment_id INT NOT NULL,
    port_number INT NOT NULL,
    asset_id INT NULL,
    CONSTRAINT fk_rack_port_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_rack_port_equipment FOREIGN KEY (equipment_id) REFERENCES rack_equipment(id) ON DELETE CASCADE,
    CONSTRAINT fk_rack_port_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL,
    CONSTRAINT uq_rack_equipment_port UNIQUE (equipment_id, port_number)
);
