-- Matrix.org Chat Integration
-- Tables for in-portal chat backed by a Matrix homeserver.

CREATE TABLE IF NOT EXISTS chat_rooms (
  id INT AUTO_INCREMENT PRIMARY KEY,
  matrix_room_id VARCHAR(255) UNIQUE,
  room_alias VARCHAR(255),
  created_by_user_id INT NOT NULL,
  company_id INT NOT NULL,
  subject VARCHAR(500) NOT NULL,
  status ENUM('open','closed') NOT NULL DEFAULT 'open',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_message_at DATETIME NULL,
  linked_ticket_id INT NULL
);

CREATE TABLE IF NOT EXISTS chat_room_participants (
  id INT AUTO_INCREMENT PRIMARY KEY,
  room_id INT NOT NULL,
  user_id INT NULL,
  matrix_user_id VARCHAR(255) NOT NULL,
  role ENUM('creator','technician','admin','guest') NOT NULL,
  joined_at DATETIME NOT NULL,
  left_at DATETIME NULL,
  UNIQUE KEY uq_room_matrix_user (room_id, matrix_user_id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id INT AUTO_INCREMENT PRIMARY KEY,
  room_id INT NOT NULL,
  matrix_event_id VARCHAR(255) UNIQUE,
  sender_matrix_id VARCHAR(255) NOT NULL,
  sender_user_id INT NULL,
  body TEXT,
  msgtype VARCHAR(64) NOT NULL DEFAULT 'm.text',
  sent_at DATETIME NOT NULL,
  redacted_at DATETIME NULL
);

CREATE TABLE IF NOT EXISTS chat_invites (
  id INT AUTO_INCREMENT PRIMARY KEY,
  room_id INT NOT NULL,
  created_by_user_id INT NOT NULL,
  target_email VARCHAR(255) NULL,
  target_phone VARCHAR(32) NULL,
  target_display_name VARCHAR(255) NULL,
  provisioned_matrix_user_id VARCHAR(255) NULL,
  invite_token VARCHAR(128) NOT NULL,
  temporary_password_hash TEXT NULL,
  delivery_method ENUM('email','sms','manual') NOT NULL DEFAULT 'manual',
  status ENUM('pending','sent','accepted','expired','revoked') NOT NULL DEFAULT 'pending',
  expires_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  UNIQUE KEY uq_invite_token (invite_token)
);

CREATE TABLE IF NOT EXISTS chat_user_links (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  email VARCHAR(255) NULL,
  matrix_user_id VARCHAR(255) NOT NULL,
  access_token_encrypted TEXT NULL,
  device_id VARCHAR(255) NULL,
  is_provisioned TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uq_chat_user_mxid (matrix_user_id)
);

CREATE TABLE IF NOT EXISTS matrix_sync_state (
  id INT NOT NULL DEFAULT 1 PRIMARY KEY,
  next_batch TEXT NULL,
  updated_at DATETIME NULL
);

-- Indexes (guarded with IF NOT EXISTS for idempotency via MySQL 8+ syntax)
-- Use CREATE INDEX only when index does not exist; wrapped in stored procedure for idempotency.
CREATE INDEX IF NOT EXISTS idx_chat_rooms_company_id ON chat_rooms (company_id);
CREATE INDEX IF NOT EXISTS idx_chat_rooms_status ON chat_rooms (status);
CREATE INDEX IF NOT EXISTS idx_chat_room_participants_room_id ON chat_room_participants (room_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_room_id ON chat_messages (room_id);
CREATE INDEX IF NOT EXISTS idx_chat_invites_room_id ON chat_invites (room_id);
