-- Matrix Bot AI Waiting Assistant
-- Tracks waiting-assistant state on chat rooms and persists Ollama analysis work.

ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS ai_bot_response_count INT NOT NULL DEFAULT 0;
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS ai_last_bot_response_at DATETIME NULL;
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS ai_last_analysis_at DATETIME NULL;
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS ai_last_user_message_at DATETIME NULL;
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS ai_extracted_keywords JSON NULL;
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS ai_matched_articles JSON NULL;
ALTER TABLE chat_rooms ADD COLUMN IF NOT EXISTS ai_last_confidence DECIMAL(5,2) NULL;

CREATE TABLE IF NOT EXISTS matrix_ai_analysis_queue (
  id INT AUTO_INCREMENT PRIMARY KEY,
  queue_identifier VARCHAR(64) NOT NULL UNIQUE,
  chat_room_id INT NOT NULL,
  created_at DATETIME NOT NULL,
  last_attempt_at DATETIME NULL,
  retry_count INT NOT NULL DEFAULT 0,
  expires_at DATETIME NOT NULL,
  status ENUM('queued','processing','completed','cancelled','timed_out','failed') NOT NULL DEFAULT 'queued',
  cancellation_reason VARCHAR(255) NULL,
  next_attempt_at DATETIME NOT NULL,
  created_for_response_number INT NOT NULL DEFAULT 2,
  analysis_payload JSON NULL,
  result_payload JSON NULL
);

CREATE INDEX IF NOT EXISTS idx_matrix_ai_queue_room_status ON matrix_ai_analysis_queue (chat_room_id, status);
CREATE INDEX IF NOT EXISTS idx_matrix_ai_queue_due ON matrix_ai_analysis_queue (status, next_attempt_at, expires_at);
CREATE INDEX IF NOT EXISTS idx_chat_rooms_ai_waiting ON chat_rooms (status, assigned_tech_user_id, ai_last_user_message_at, ai_bot_response_count);
