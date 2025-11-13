-- Create ticket attachments table
-- Allows files to be uploaded to tickets with different access levels
CREATE TABLE IF NOT EXISTS ticket_attachments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ticket_id INT NOT NULL,
    filename VARCHAR(255) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    file_size BIGINT UNSIGNED NOT NULL,
    mime_type VARCHAR(127) NULL,
    access_level VARCHAR(32) NOT NULL DEFAULT 'closed',
    uploaded_by_user_id INT NULL,
    uploaded_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX idx_ticket_attachments_ticket_id (ticket_id),
    INDEX idx_ticket_attachments_uploaded_by (uploaded_by_user_id),
    INDEX idx_ticket_attachments_access_level (access_level),
    CONSTRAINT fk_ticket_attachments_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
    CONSTRAINT fk_ticket_attachments_uploader FOREIGN KEY (uploaded_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT chk_ticket_attachments_access_level CHECK (access_level IN ('open', 'closed', 'restricted'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
