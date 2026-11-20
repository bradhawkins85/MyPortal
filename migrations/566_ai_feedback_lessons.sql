-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Text learning: guidance MyPortal adds to AI prompts when technicians keep
-- giving the same reason for 👎. Built nightly from reason codes and counts
-- only (never output text); ignored_at is an admin Undo that rebuilds keep.
CREATE TABLE IF NOT EXISTS ai_feedback_lessons (
    id INT AUTO_INCREMENT PRIMARY KEY,
    feature VARCHAR(40) NOT NULL,
    reason VARCHAR(40) NOT NULL,
    votes INT NOT NULL DEFAULT 0,
    share DECIMAL(5,4) NOT NULL DEFAULT 0,
    active TINYINT(1) NOT NULL DEFAULT 0,
    ignored_at DATETIME(6) NULL DEFAULT NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uq_ai_feedback_lessons UNIQUE (feature, reason)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
