ALTER TABLE bcp_role_assignment
    ADD COLUMN IF NOT EXISTS collaborator_role ENUM('Executor', 'CoAuthor', 'Reviewer', 'Approver') NOT NULL DEFAULT 'Executor' AFTER user_id;

ALTER TABLE bcp_training_item
    ADD COLUMN IF NOT EXISTS status ENUM('Scheduled', 'Completed', 'Cancelled') NOT NULL DEFAULT 'Scheduled' AFTER training_type,
    ADD COLUMN IF NOT EXISTS participants_count INT NULL AFTER status,
    ADD COLUMN IF NOT EXISTS score_percent INT NULL AFTER participants_count,
    ADD COLUMN IF NOT EXISTS lessons_learned TEXT NULL AFTER comments,
    ADD COLUMN IF NOT EXISTS follow_up_actions TEXT NULL AFTER lessons_learned,
    ADD CONSTRAINT ck_bcp_training_participants_non_negative CHECK (participants_count >= 0 OR participants_count IS NULL),
    ADD CONSTRAINT ck_bcp_training_score_range CHECK (score_percent >= 0 AND score_percent <= 100 OR score_percent IS NULL);

ALTER TABLE bcp_incident
    ADD COLUMN IF NOT EXISTS closed_at DATETIME NULL AFTER source,
    ADD COLUMN IF NOT EXISTS after_action_summary TEXT NULL AFTER closed_at,
    ADD COLUMN IF NOT EXISTS after_action_improvements TEXT NULL AFTER after_action_summary,
    ADD COLUMN IF NOT EXISTS after_action_reviewed_at DATETIME NULL AFTER after_action_improvements;

ALTER TABLE bcp_review_item
    ADD COLUMN IF NOT EXISTS version_label VARCHAR(50) NULL AFTER review_date,
    ADD COLUMN IF NOT EXISTS approval_status ENUM('Draft', 'InReview', 'Approved', 'ChangesRequested') NOT NULL DEFAULT 'Draft' AFTER version_label,
    ADD COLUMN IF NOT EXISTS reviewed_by_user_id INT NULL AFTER approval_status,
    ADD COLUMN IF NOT EXISTS approved_by_user_id INT NULL AFTER reviewed_by_user_id,
    ADD COLUMN IF NOT EXISTS approval_snapshot TEXT NULL AFTER changes_made;

CREATE TABLE IF NOT EXISTS bcp_dependency_map (
  id INT PRIMARY KEY AUTO_INCREMENT,
  plan_id INT NOT NULL,
  critical_activity_id INT NULL,
  dependency_type ENUM('Vendor', 'Resource') NOT NULL COMMENT 'Dependency classification',
  dependency_name VARCHAR(255) NOT NULL COMMENT 'Name of the vendor or resource',
  owner_name VARCHAR(255) NULL COMMENT 'Dependency owner or contact',
  rto_hours INT NULL COMMENT 'Dependency recovery target in hours',
  notes TEXT NULL COMMENT 'Dependency notes and failure impact',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  FOREIGN KEY (critical_activity_id) REFERENCES bcp_critical_activity(id) ON DELETE SET NULL,
  INDEX idx_bcp_dependency_plan (plan_id),
  INDEX idx_bcp_dependency_activity (critical_activity_id),
  INDEX idx_bcp_dependency_type (dependency_type),
  CONSTRAINT ck_bcp_dependency_rto_non_negative CHECK (rto_hours >= 0 OR rto_hours IS NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
