-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 436: Power supplies (PSUs) become ports fed from another unit's outlet.
ALTER TABLE rack_equipment_ports ADD COLUMN source_port_id INT NULL;
-- Carry each documented PDU/UPS power source over as that unit's PSU 1.
-- The legacy power_source_* columns are left in place for rollback.
INSERT INTO rack_equipment_ports (company_id, equipment_id, port_number, connector, label, source_port_id)
    SELECT e.company_id, e.id,
           COALESCE((SELECT MAX(p.port_number) FROM rack_equipment_ports p WHERE p.equipment_id = e.id), 0) + 1,
           'psu', e.power_source_label, e.power_source_port_id
    FROM rack_equipment e
    WHERE e.power_source_port_id IS NOT NULL OR e.power_source_label IS NOT NULL;
UPDATE rack_equipment SET port_count = port_count + 1
    WHERE power_source_port_id IS NOT NULL OR power_source_label IS NOT NULL;
