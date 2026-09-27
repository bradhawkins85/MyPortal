-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

ALTER TABLE racks ADD COLUMN depth_mm INT NOT NULL DEFAULT 1000;
ALTER TABLE rack_equipment ADD COLUMN width_lanes INT NOT NULL DEFAULT 3;
ALTER TABLE rack_equipment ADD COLUMN start_lane INT NOT NULL DEFAULT 1;
ALTER TABLE rack_equipment ADD COLUMN depth_mode VARCHAR(16) NOT NULL DEFAULT 'half';

CREATE TABLE IF NOT EXISTS rack_equipment_slots (
    equipment_id INT NOT NULL,
    rack_id INT NOT NULL,
    unit_number INT NOT NULL,
    face VARCHAR(8) NOT NULL,
    lane INT NOT NULL,
    PRIMARY KEY (rack_id, unit_number, face, lane),
    CONSTRAINT fk_rack_slot_equipment FOREIGN KEY (equipment_id) REFERENCES rack_equipment(id) ON DELETE CASCADE,
    CONSTRAINT fk_rack_slot_rack FOREIGN KEY (rack_id) REFERENCES racks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO rack_equipment_slots (equipment_id, rack_id, unit_number, face, lane)
SELECT u.equipment_id, u.rack_id, u.unit_number, u.face, lanes.lane
FROM rack_equipment_units u
JOIN (SELECT 1 lane UNION ALL SELECT 2 UNION ALL SELECT 3) lanes
LEFT JOIN rack_equipment_slots s ON s.rack_id=u.rack_id AND s.unit_number=u.unit_number
 AND s.face=u.face AND s.lane=lanes.lane
WHERE s.equipment_id IS NULL;
