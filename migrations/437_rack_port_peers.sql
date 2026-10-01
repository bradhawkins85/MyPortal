-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 437: Port-to-port links between rack items (e.g. a server NIC to a
-- switch port). Both ends store the other's id so each side shows the link.
ALTER TABLE rack_equipment_ports ADD COLUMN peer_port_id INT NULL;
