-- BC3/BC4 Business Continuity Planning Data Model
-- SQLAlchemy 2.0 async models implemented via SQL migration
-- Supports template-driven, versioned BCP with review/approval workflow and attachments
--
-- COMPATIBILITY: This migration is designed for MySQL/MariaDB
-- Features used: AUTO_INCREMENT, ENUM, JSON, ON UPDATE CURRENT_TIMESTAMP, ENGINE=InnoDB
-- For SQLite compatibility notes, see migrations/README_BC4_COMPATIBILITY.md
--
-- IDEMPOTENCY: Safe to run multiple times - uses CREATE TABLE IF NOT EXISTS
-- DATA PRESERVATION: No DROP or TRUNCATE statements - existing data is retained

-- BC Template: Defines structure for business continuity plans
CREATE TABLE IF NOT EXISTS bc_template (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  version VARCHAR(50) NOT NULL,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  schema_json JSON COMMENT 'Section and field definitions as JSON',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_bc_template_default (is_default)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BC Section Definition: Optional granular section definitions for templates
CREATE TABLE IF NOT EXISTS bc_section_definition (
  id INT AUTO_INCREMENT PRIMARY KEY,
  template_id INT NOT NULL,
  `key` VARCHAR(100) NOT NULL COMMENT 'Unique section key within template',
  title VARCHAR(255) NOT NULL,
  order_index INT NOT NULL DEFAULT 0,
  schema_json JSON COMMENT 'Field definitions for this section',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (template_id) REFERENCES bc_template(id) ON DELETE CASCADE,
  INDEX idx_bc_section_template (template_id),
  INDEX idx_bc_section_template_order (template_id, order_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BC Plan Version: Must be created before bc_plan due to current_version_id reference
CREATE TABLE IF NOT EXISTS bc_plan_version (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  version_number INT NOT NULL,
  status ENUM('active', 'superseded') NOT NULL DEFAULT 'active',
  authored_by_user_id INT NOT NULL,
  authored_at_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  summary_change_note TEXT,
  content_json JSON COMMENT 'Section data as JSON',
  docx_export_hash VARCHAR(64),
  pdf_export_hash VARCHAR(64),
  INDEX idx_bc_plan_version_plan (plan_id),
  INDEX idx_bc_plan_version_plan_status (plan_id, status),
  INDEX idx_bc_plan_version_authored (authored_by_user_id),
  CONSTRAINT ck_version_number_positive CHECK (version_number > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BC Plan: Main business continuity plan table
CREATE TABLE IF NOT EXISTS bc_plan (
  id INT AUTO_INCREMENT PRIMARY KEY,
  org_id INT COMMENT 'For multi-tenant support',
  title VARCHAR(255) NOT NULL,
  status ENUM('draft', 'in_review', 'approved', 'archived') NOT NULL DEFAULT 'draft',
  template_id INT,
  current_version_id INT,
  owner_user_id INT NOT NULL,
  approved_at_utc DATETIME,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (template_id) REFERENCES bc_template(id) ON DELETE SET NULL,
  FOREIGN KEY (current_version_id) REFERENCES bc_plan_version(id) ON DELETE SET NULL,
  INDEX idx_bc_plan_org_status (org_id, status),
  INDEX idx_bc_plan_status_updated (status, updated_at),
  INDEX idx_bc_plan_template (template_id),
  INDEX idx_bc_plan_owner (owner_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add foreign key from bc_plan_version to bc_plan (circular reference)
ALTER TABLE bc_plan_version 
  ADD CONSTRAINT fk_bc_plan_version_plan 
  FOREIGN KEY (plan_id) REFERENCES bc_plan(id) ON DELETE CASCADE;

-- BC Contact: Emergency contacts for plans
CREATE TABLE IF NOT EXISTS bc_contact (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  role VARCHAR(255),
  phone VARCHAR(50),
  email VARCHAR(255),
  notes TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bc_plan(id) ON DELETE CASCADE,
  INDEX idx_bc_contact_plan (plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BC Process: Critical business processes with recovery objectives
CREATE TABLE IF NOT EXISTS bc_process (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  rto_minutes INT COMMENT 'Recovery Time Objective in minutes',
  rpo_minutes INT COMMENT 'Recovery Point Objective in minutes',
  mtpd_minutes INT COMMENT 'Maximum Tolerable Period of Disruption in minutes',
  impact_rating VARCHAR(50) COMMENT 'e.g., critical, high, medium, low',
  dependencies_json JSON COMMENT 'Process dependencies as JSON',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bc_plan(id) ON DELETE CASCADE,
  INDEX idx_bc_process_plan (plan_id),
  INDEX idx_bc_process_impact (impact_rating),
  CONSTRAINT ck_rto_non_negative CHECK (rto_minutes >= 0 OR rto_minutes IS NULL),
  CONSTRAINT ck_rpo_non_negative CHECK (rpo_minutes >= 0 OR rpo_minutes IS NULL),
  CONSTRAINT ck_mtpd_non_negative CHECK (mtpd_minutes >= 0 OR mtpd_minutes IS NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BC Risk: Risk assessments for plans
CREATE TABLE IF NOT EXISTS bc_risk (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  threat VARCHAR(500) NOT NULL,
  likelihood VARCHAR(50) COMMENT 'e.g., rare, unlikely, possible, likely, almost_certain',
  impact VARCHAR(50) COMMENT 'e.g., insignificant, minor, moderate, major, catastrophic',
  rating VARCHAR(50) COMMENT 'Overall risk rating, e.g., low, medium, high, critical',
  mitigation TEXT,
  owner_user_id INT COMMENT 'Risk owner',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bc_plan(id) ON DELETE CASCADE,
  INDEX idx_bc_risk_plan (plan_id),
  INDEX idx_bc_risk_rating (rating),
  INDEX idx_bc_risk_owner (owner_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BC Attachment: File attachments for plans
CREATE TABLE IF NOT EXISTS bc_attachment (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  file_name VARCHAR(255) NOT NULL,
  storage_path VARCHAR(500) NOT NULL COMMENT 'Path in storage system',
  content_type VARCHAR(100),
  size_bytes INT,
  uploaded_by_user_id INT NOT NULL,
  uploaded_at_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  hash VARCHAR(64) COMMENT 'SHA256 hash for integrity verification',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bc_plan(id) ON DELETE CASCADE,
  INDEX idx_bc_attachment_plan (plan_id),
  INDEX idx_bc_attachment_uploaded_by (uploaded_by_user_id),
  CONSTRAINT ck_size_non_negative CHECK (size_bytes >= 0 OR size_bytes IS NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BC Review: Review and approval workflow
CREATE TABLE IF NOT EXISTS bc_review (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  requested_by_user_id INT NOT NULL,
  reviewer_user_id INT NOT NULL,
  status ENUM('pending', 'approved', 'changes_requested') NOT NULL DEFAULT 'pending',
  requested_at_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  decided_at_utc DATETIME,
  notes TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bc_plan(id) ON DELETE CASCADE,
  INDEX idx_bc_review_plan (plan_id),
  INDEX idx_bc_review_reviewer (reviewer_user_id),
  INDEX idx_bc_review_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BC Acknowledgment: User acknowledgments of plan reviews
CREATE TABLE IF NOT EXISTS bc_ack (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  user_id INT NOT NULL,
  ack_at_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ack_version_number INT COMMENT 'Version number acknowledged',
  FOREIGN KEY (plan_id) REFERENCES bc_plan(id) ON DELETE CASCADE,
  INDEX idx_bc_ack_plan (plan_id),
  INDEX idx_bc_ack_user (user_id),
  INDEX idx_bc_ack_plan_user (plan_id, user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BC Audit: Audit trail for plan changes
CREATE TABLE IF NOT EXISTS bc_audit (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  action VARCHAR(100) NOT NULL COMMENT 'e.g., created, updated, approved, archived',
  actor_user_id INT NOT NULL,
  details_json JSON COMMENT 'Additional audit details as JSON',
  at_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bc_plan(id) ON DELETE CASCADE,
  INDEX idx_bc_audit_plan (plan_id),
  INDEX idx_bc_audit_actor (actor_user_id),
  INDEX idx_bc_audit_action (action),
  INDEX idx_bc_audit_at (at_utc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BC Change Log Map: Links change log files to plans
CREATE TABLE IF NOT EXISTS bc_change_log_map (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  change_guid VARCHAR(36) NOT NULL COMMENT 'GUID referencing change log file',
  imported_at_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bc_plan(id) ON DELETE CASCADE,
  INDEX idx_bc_change_log_plan (plan_id),
  INDEX idx_bc_change_log_guid (change_guid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
