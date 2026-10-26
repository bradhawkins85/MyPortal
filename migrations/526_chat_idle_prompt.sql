-- Chat idle prompt
-- Tracks whether a customer has written anything in a chat, and when the AI
-- assistant asked an empty chat whether help is needed, so chats opened
-- without a message can be closed automatically.

ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS ai_customer_message_at DATETIME NULL;
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS ai_idle_prompt_sent_at DATETIME NULL;

-- Existing chats that already hold messages keep the normal acknowledgement.
UPDATE chat_rooms
   SET ai_customer_message_at = COALESCE(ai_last_user_message_at, last_message_at, updated_at)
 WHERE ai_customer_message_at IS NULL
   AND EXISTS (SELECT 1 FROM chat_messages m WHERE m.room_id = chat_rooms.id);
