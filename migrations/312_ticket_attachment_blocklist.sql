CREATE TABLE IF NOT EXISTS ticket_attachment_blocklist (
  id INT AUTO_INCREMENT PRIMARY KEY,
  sha256_hash CHAR(64) NOT NULL,
  original_filename VARCHAR(255) NULL,
  file_size BIGINT NULL,
  mime_type VARCHAR(255) NULL,
  created_by_user_id INT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_ticket_attachment_blocklist_hash (sha256_hash),
  CONSTRAINT fk_ticket_attachment_blocklist_user
    FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);
