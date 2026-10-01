-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 438: Network map. Assets get a catalogue type (derived for synced
-- assets unless a technician overrides it), network and radio interfaces, and
-- links between interfaces and rack ports, including point-to-point wireless.
ALTER TABLE assets ADD COLUMN asset_type VARCHAR(32) NULL;
ALTER TABLE assets ADD COLUMN asset_type_source VARCHAR(8) NOT NULL DEFAULT 'auto';

CREATE TABLE IF NOT EXISTS asset_interfaces (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    asset_id INT NOT NULL,
    name VARCHAR(64) NOT NULL,
    kind VARCHAR(16) NOT NULL DEFAULT 'ethernet',
    mac_address VARCHAR(32) NULL,
    ip_address_id INT NULL,
    speed_mbps INT NULL,
    vlan VARCHAR(64) NULL,
    radio_mode VARCHAR(16) NULL,
    frequency_mhz INT NULL,
    channel_width_mhz INT NULL,
    ssid VARCHAR(64) NULL,
    azimuth_deg INT NULL,
    tx_power_dbm INT NULL,
    antenna_gain_dbi INT NULL,
    notes VARCHAR(255) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_asset_interface_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_asset_interface_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
    CONSTRAINT fk_asset_interface_ip FOREIGN KEY (ip_address_id) REFERENCES ip_addresses(id) ON DELETE SET NULL,
    CONSTRAINT uq_asset_interface_name UNIQUE (asset_id, name)
);

CREATE TABLE IF NOT EXISTS network_links (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    a_kind VARCHAR(12) NOT NULL,
    a_id INT NOT NULL,
    b_kind VARCHAR(12) NOT NULL,
    b_id INT NOT NULL,
    medium VARCHAR(16) NOT NULL DEFAULT 'copper',
    label VARCHAR(191) NULL,
    speed_mbps INT NULL,
    distance_m INT NULL,
    frequency_mhz INT NULL,
    signal_dbm INT NULL,
    notes VARCHAR(255) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_network_link_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);
CREATE INDEX idx_network_links_a ON network_links (a_kind, a_id);
CREATE INDEX idx_network_links_b ON network_links (b_kind, b_id);
