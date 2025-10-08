CREATE TABLE IF NOT EXISTS ports (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    code VARCHAR(20) NOT NULL,
    country VARCHAR(100) NOT NULL,
    region VARCHAR(100) NULL,
    timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
    description TEXT NULL,
    latitude DECIMAL(9,6) NULL,
    longitude DECIMAL(9,6) NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ports_code (code),
    KEY idx_ports_country (country),
    KEY idx_ports_is_active (is_active)
);

CREATE TABLE IF NOT EXISTS port_documents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    port_id INT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    storage_path VARCHAR(512) NOT NULL,
    content_type VARCHAR(255) NULL,
    file_size BIGINT NOT NULL,
    description VARCHAR(255) NULL,
    uploaded_by INT NULL,
    uploaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (port_id) REFERENCES ports (id) ON DELETE CASCADE,
    FOREIGN KEY (uploaded_by) REFERENCES users (id) ON DELETE SET NULL,
    KEY idx_port_documents_port_id (port_id)
);

CREATE TABLE IF NOT EXISTS port_pricing_versions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    port_id INT NOT NULL,
    version_label VARCHAR(100) NOT NULL,
    status ENUM('draft','pending_review','approved','rejected') NOT NULL DEFAULT 'draft',
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    base_rate DECIMAL(12,2) NOT NULL DEFAULT 0,
    handling_rate DECIMAL(12,2) NOT NULL DEFAULT 0,
    storage_rate DECIMAL(12,2) NOT NULL DEFAULT 0,
    notes TEXT NULL,
    submitted_by INT NULL,
    approved_by INT NULL,
    submitted_at TIMESTAMP NULL,
    approved_at TIMESTAMP NULL,
    rejection_reason TEXT NULL,
    effective_from DATE NULL,
    effective_to DATE NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (port_id) REFERENCES ports (id) ON DELETE CASCADE,
    FOREIGN KEY (submitted_by) REFERENCES users (id) ON DELETE SET NULL,
    FOREIGN KEY (approved_by) REFERENCES users (id) ON DELETE SET NULL,
    UNIQUE KEY uq_port_pricing_version_label (port_id, version_label),
    KEY idx_port_pricing_status (status),
    KEY idx_port_pricing_effective_from (effective_from)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NULL,
    event_type VARCHAR(100) NOT NULL,
    message TEXT NOT NULL,
    metadata JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP NULL,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL,
    KEY idx_notifications_user_id (user_id),
    KEY idx_notifications_read_at (read_at)
);
