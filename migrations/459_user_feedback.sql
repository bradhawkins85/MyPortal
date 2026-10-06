-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 459: Add user_feedback table for in-app feedback submissions.
--
-- Captures the 👍/👎 rating, reason and suggested improvements from the
-- feedback widget in the top menu bar, and stores the id of the follow-up
-- ticket created for the support team. Mirrors migration 453's cross-database
-- style (DATETIME created_at with a CURRENT_TIMESTAMP default, CONSTRAINT
-- foreign keys, separate CREATE INDEX statement).

CREATE TABLE IF NOT EXISTS user_feedback (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    ticket_id INT NULL,
    rating VARCHAR(4) NOT NULL,
    reason TEXT NULL,
    suggested_improvements TEXT NULL,
    page_url TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_user_feedback_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_feedback_ticket FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_user_feedback_user_id ON user_feedback (user_id);
CREATE INDEX idx_user_feedback_created_at ON user_feedback (created_at);