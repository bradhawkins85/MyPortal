-- Tray Chat Popup: one-time URL tokens that authenticate a tray device
-- when it opens the in-app chat popup without a browser login.
--
-- Each token is issued by POST /api/tray/chat-token (device-auth) and is
-- consumed exactly once by GET /tray/chat?token=...  A token may be
-- pre-bound to a room_id (for technician-initiated chats) or left NULL
-- (for user-initiated chats, where the room is created on first open).

CREATE TABLE IF NOT EXISTS tray_chat_tokens (
  id INT AUTO_INCREMENT PRIMARY KEY,
  device_id INT NOT NULL,
  token_hash VARCHAR(128) NOT NULL,
  room_id INT NULL,
  expires_at DATETIME NOT NULL,
  used_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_tray_chat_token_hash UNIQUE (token_hash)
);

CREATE INDEX IF NOT EXISTS idx_tray_chat_tokens_device ON tray_chat_tokens (device_id);
CREATE INDEX IF NOT EXISTS idx_tray_chat_tokens_expires ON tray_chat_tokens (expires_at);

-- Allow tray-device-initiated rooms (no portal user on the other end yet).
ALTER TABLE chat_rooms MODIFY COLUMN created_by_user_id INT NULL;
