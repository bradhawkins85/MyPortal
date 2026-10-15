-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Observed device addresses do not require an IPAM network or reservation.
ALTER TABLE asset_interfaces ADD COLUMN observed_ip_address VARCHAR(45) NULL;
