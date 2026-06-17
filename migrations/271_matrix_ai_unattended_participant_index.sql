-- Matrix AI waiting assistant now treats a room as attended once a technician
-- or admin participant has joined the Matrix-backed chat.  Index the lookup
-- used by the unattended-room scanner and eligibility checks.

CREATE INDEX IF NOT EXISTS idx_chat_room_participants_room_role
  ON chat_room_participants (room_id, role);
