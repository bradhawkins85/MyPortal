-- Matrix AI waiting assistant now treats a room as attended only after a
-- technician/admin sends a chat message.  Index the message lookup used by
-- the unattended-room scanner and eligibility checks.

CREATE INDEX IF NOT EXISTS idx_chat_messages_room_sender_user_matrix
  ON chat_messages (room_id, sender_user_id, sender_matrix_id, redacted_at);
