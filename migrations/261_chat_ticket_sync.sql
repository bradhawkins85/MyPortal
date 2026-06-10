-- Link Matrix chat messages and ticket replies so public conversation sync can
-- run in both directions without duplicating messages.

CREATE TABLE IF NOT EXISTS chat_ticket_reply_links (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    room_id INT NOT NULL,
    ticket_id INT NOT NULL,
    chat_message_id INT NULL,
    ticket_reply_id INT NULL,
    sync_direction VARCHAR(32) NOT NULL,
    created_at DATETIME NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_ticket_link_message ON chat_ticket_reply_links (chat_message_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_ticket_link_reply ON chat_ticket_reply_links (ticket_reply_id);
CREATE INDEX IF NOT EXISTS idx_chat_ticket_link_room ON chat_ticket_reply_links (room_id);
CREATE INDEX IF NOT EXISTS idx_chat_ticket_link_ticket ON chat_ticket_reply_links (ticket_id);
