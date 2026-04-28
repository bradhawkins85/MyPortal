-- Add e2ee_enabled flag to chat_rooms so the portal can track which rooms
-- have Matrix end-to-end encryption enabled.

ALTER TABLE chat_rooms
  ADD COLUMN IF NOT EXISTS e2ee_enabled TINYINT(1) NOT NULL DEFAULT 0;
