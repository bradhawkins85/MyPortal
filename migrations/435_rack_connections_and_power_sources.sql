-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 435: Connector types and labels for rack item ports, plus power source links.
ALTER TABLE rack_equipment_ports ADD COLUMN connector VARCHAR(8) NOT NULL DEFAULT 'data';
ALTER TABLE rack_equipment_ports ADD COLUMN label VARCHAR(191) NULL;
UPDATE rack_equipment_ports SET connector = 'iec'
    WHERE equipment_id IN (SELECT id FROM rack_equipment WHERE item_type = 'pdu');
ALTER TABLE rack_equipment ADD COLUMN power_source_port_id INT NULL;
ALTER TABLE rack_equipment ADD COLUMN power_source_label VARCHAR(191) NULL;
