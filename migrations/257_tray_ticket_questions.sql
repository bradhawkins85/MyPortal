-- Migration 257: Tray ticket dynamic questions
-- Adds tables for per-company and global dynamic intake questions shown in
-- the tray Submit Ticket dialog, including conditional follow-up logic and
-- a snapshot of submitted answers linked to the created ticket.
--
-- Idempotent: uses CREATE TABLE IF NOT EXISTS / ALTER TABLE IF NOT EXISTS.

-- Question definitions.  scope = 'global' means visible to all companies;
-- scope = 'company' means visible only to devices enrolled under company_id.
CREATE TABLE IF NOT EXISTS tray_ticket_questions (
  id INT PRIMARY KEY AUTO_INCREMENT,
  scope VARCHAR(16) NOT NULL DEFAULT 'global',
  company_id INT NULL,
  field_type VARCHAR(16) NOT NULL DEFAULT 'text',
  label VARCHAR(255) NOT NULL,
  placeholder VARCHAR(255) NULL,
  is_required TINYINT(1) NOT NULL DEFAULT 0,
  options_json TEXT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tray_ticket_questions_scope
  ON tray_ticket_questions (scope, company_id, is_active, sort_order);

-- Conditional visibility rules.  A question is only shown when ALL of its
-- conditions are satisfied.  Multiple rows with the same question_id are
-- ANDed together.  Supported operators: equals, not_equals, contains.
CREATE TABLE IF NOT EXISTS tray_ticket_question_conditions (
  id INT PRIMARY KEY AUTO_INCREMENT,
  question_id INT NOT NULL,
  parent_question_id INT NOT NULL,
  operator VARCHAR(16) NOT NULL DEFAULT 'equals',
  expected_value VARCHAR(255) NOT NULL DEFAULT '',
  CONSTRAINT fk_ttqc_question FOREIGN KEY (question_id)
    REFERENCES tray_ticket_questions (id) ON DELETE CASCADE,
  CONSTRAINT fk_ttqc_parent FOREIGN KEY (parent_question_id)
    REFERENCES tray_ticket_questions (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tray_ticket_question_conditions_question
  ON tray_ticket_question_conditions (question_id);

-- Submitted answer snapshot.  question_label_snapshot and
-- is_required_snapshot capture the definition state at submission time so
-- audit records remain readable even if the definition is later edited or
-- deleted.
CREATE TABLE IF NOT EXISTS tray_ticket_answers (
  id INT PRIMARY KEY AUTO_INCREMENT,
  ticket_id INT NOT NULL,
  question_id INT NULL,
  question_label_snapshot VARCHAR(255) NOT NULL,
  is_required_snapshot TINYINT(1) NOT NULL DEFAULT 0,
  answer_value TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tray_ticket_answers_ticket
  ON tray_ticket_answers (ticket_id);
