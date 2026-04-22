-- Add sender_display_name to chat_messages so the real user's name is stored
-- and shown instead of the raw Matrix user ID (e.g. @myportal:matrix).
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS sender_display_name VARCHAR(255) NULL;
