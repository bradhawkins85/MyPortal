-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Store attachment details (Matrix content URL, type, size, file name, image
-- size and shared location) for chat messages, so photos, videos and files
-- can be shown in and sent from the tray chat window.
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS media_json TEXT NULL;
