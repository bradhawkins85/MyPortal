-- Add assigned technician tracking to chat_rooms
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS assigned_tech_user_id INT NULL;
CREATE INDEX IF NOT EXISTS idx_chat_rooms_assigned_tech ON chat_rooms (assigned_tech_user_id);
