-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

ALTER TABLE ip_addresses
    ADD COLUMN source_network_device_id INT NULL AFTER asset_id,
    ADD CONSTRAINT fk_ip_address_source_device
        FOREIGN KEY (source_network_device_id) REFERENCES network_devices(id) ON DELETE SET NULL;

CREATE INDEX ix_ip_address_source_device ON ip_addresses (source_network_device_id);
