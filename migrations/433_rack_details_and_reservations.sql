-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

ALTER TABLE racks ADD COLUMN starting_unit INT NOT NULL DEFAULT 1;
ALTER TABLE racks ADD COLUMN numbering_direction VARCHAR(16) NOT NULL DEFAULT 'bottom-up';
ALTER TABLE racks ADD COLUMN width_mm INT NULL;
ALTER TABLE racks ADD COLUMN asset_tag VARCHAR(191) NULL;
ALTER TABLE racks ADD COLUMN serial_number VARCHAR(191) NULL;
ALTER TABLE racks ADD COLUMN description VARCHAR(1000) NULL;
ALTER TABLE racks ADD COLUMN power_capacity_watts INT NULL;
ALTER TABLE rack_equipment ADD COLUMN power_draw_watts INT NULL;

CREATE TABLE IF NOT EXISTS rack_reservations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    rack_id INT NOT NULL,
    start_unit INT NOT NULL,
    unit_height INT NOT NULL DEFAULT 1,
    face VARCHAR(8) NOT NULL DEFAULT 'front',
    width_lanes INT NOT NULL DEFAULT 3,
    start_lane INT NOT NULL DEFAULT 1,
    depth_mode VARCHAR(16) NOT NULL DEFAULT 'half',
    label VARCHAR(191) NULL,
    owner VARCHAR(191) NULL,
    notes VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rack_reservation_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_rack_reservation_rack FOREIGN KEY (rack_id) REFERENCES racks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rack_reservation_slots (
    reservation_id INT NOT NULL,
    rack_id INT NOT NULL,
    unit_number INT NOT NULL,
    face VARCHAR(8) NOT NULL,
    lane INT NOT NULL,
    PRIMARY KEY (rack_id, unit_number, face, lane),
    CONSTRAINT fk_rack_reservation_slot FOREIGN KEY (reservation_id) REFERENCES rack_reservations(id) ON DELETE CASCADE,
    CONSTRAINT fk_rack_reservation_slot_rack FOREIGN KEY (rack_id) REFERENCES racks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
