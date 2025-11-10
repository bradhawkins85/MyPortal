-- BC02: Business Continuity Planning - Comprehensive Data Model
-- Multi-tenant tables with company_id for all core entities
-- Covers: Core, Risk & Preparedness, BIA, Incident Response, Recovery
--
-- COMPATIBILITY: This migration is designed for MySQL/MariaDB
-- Features used: AUTO_INCREMENT, ENUM, ON UPDATE CURRENT_TIMESTAMP, ENGINE=InnoDB
--
-- IDEMPOTENCY: Safe to run multiple times - uses CREATE TABLE IF NOT EXISTS
-- DATA PRESERVATION: No DROP or TRUNCATE statements - existing data is retained

-- ============================================================================
-- Core Entities
-- ============================================================================

-- BCP Plan: Main plan table with company-level multi-tenancy
CREATE TABLE IF NOT EXISTS bcp_plan (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL COMMENT 'Multi-tenant: company this plan belongs to',
  title VARCHAR(255) NOT NULL,
  executive_summary TEXT,
  objectives TEXT COMMENT 'Plan objectives and goals',
  version VARCHAR(50) COMMENT 'Plan version number',
  last_reviewed_at DATETIME COMMENT 'Last review date',
  next_review_at DATETIME COMMENT 'Next scheduled review date',
  distribution_notes TEXT COMMENT 'Notes about plan distribution',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_bcp_plan_company (company_id),
  INDEX idx_bcp_plan_next_review (next_review_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Distribution Entry: Tracks physical/electronic copies distributed
CREATE TABLE IF NOT EXISTS bcp_distribution_entry (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  copy_number INT NOT NULL COMMENT 'Sequential copy number',
  name VARCHAR(255) NOT NULL COMMENT 'Recipient name',
  location VARCHAR(255) COMMENT 'Storage location of copy',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_distribution_plan (plan_id),
  INDEX idx_bcp_distribution_copy (plan_id, copy_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- Risk & Preparedness
-- ============================================================================

-- BCP Risk: Risk assessment with likelihood and impact ratings
CREATE TABLE IF NOT EXISTS bcp_risk (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  description TEXT NOT NULL,
  likelihood INT COMMENT 'Likelihood rating 1-4',
  impact INT COMMENT 'Impact rating 1-4',
  rating INT COMMENT 'Computed risk rating (likelihood × impact)',
  severity VARCHAR(50) COMMENT 'Computed severity: Low, Medium, High, Extreme (denormalized)',
  preventative_actions TEXT COMMENT 'Actions to prevent risk',
  contingency_plans TEXT COMMENT 'Plans if risk materializes',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_risk_plan (plan_id),
  INDEX idx_bcp_risk_severity (severity),
  CONSTRAINT ck_likelihood_range CHECK (likelihood >= 1 AND likelihood <= 4 OR likelihood IS NULL),
  CONSTRAINT ck_impact_range CHECK (impact >= 1 AND impact <= 4 OR impact IS NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Insurance Policy: Insurance policies relevant to business continuity
CREATE TABLE IF NOT EXISTS bcp_insurance_policy (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  type VARCHAR(100) NOT NULL COMMENT 'Policy type (e.g., Property, Liability)',
  coverage TEXT COMMENT 'What is covered',
  exclusions TEXT COMMENT 'What is excluded',
  insurer VARCHAR(255) COMMENT 'Insurance company name',
  contact VARCHAR(255) COMMENT 'Contact information',
  last_review_date DATETIME COMMENT 'Last policy review date',
  payment_terms VARCHAR(255) COMMENT 'Payment schedule and terms',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_insurance_plan (plan_id),
  INDEX idx_bcp_insurance_review (last_review_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Backup Item: Data backup items and procedures
CREATE TABLE IF NOT EXISTS bcp_backup_item (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  data_scope VARCHAR(255) NOT NULL COMMENT 'What data is backed up',
  frequency VARCHAR(100) COMMENT 'Backup frequency (e.g., Daily, Weekly)',
  medium VARCHAR(100) COMMENT 'Backup medium (e.g., Cloud, Tape)',
  owner VARCHAR(255) COMMENT 'Person/team responsible',
  steps TEXT COMMENT 'Backup procedure steps',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_backup_plan (plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- Business Impact Analysis (BIA)
-- ============================================================================

-- BCP Critical Activity: Critical business activities requiring continuity planning
CREATE TABLE IF NOT EXISTS bcp_critical_activity (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  priority ENUM('High', 'Medium', 'Low') COMMENT 'Activity priority',
  supplier_dependency ENUM('None', 'Sole', 'Major', 'Many') COMMENT 'Level of supplier dependency',
  notes TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_critical_activity_plan (plan_id),
  INDEX idx_bcp_critical_activity_priority (priority)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Impact: Impact assessment for critical activities
CREATE TABLE IF NOT EXISTS bcp_impact (
  id INT AUTO_INCREMENT PRIMARY KEY,
  critical_activity_id INT NOT NULL,
  losses_financial TEXT COMMENT 'Financial impact description',
  losses_staffing TEXT COMMENT 'Staffing impact description',
  losses_reputation TEXT COMMENT 'Reputational impact description',
  fines TEXT COMMENT 'Potential fines and penalties',
  legal_liability TEXT COMMENT 'Legal liability description',
  rto_hours INT COMMENT 'Recovery Time Objective in hours',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (critical_activity_id) REFERENCES bcp_critical_activity(id) ON DELETE CASCADE,
  INDEX idx_bcp_impact_activity (critical_activity_id),
  INDEX idx_bcp_impact_rto (rto_hours),
  CONSTRAINT ck_rto_positive CHECK (rto_hours >= 0 OR rto_hours IS NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- Response (Incident)
-- ============================================================================

-- BCP Incident: Active or historical incidents triggering BCP response
CREATE TABLE IF NOT EXISTS bcp_incident (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  started_at DATETIME NOT NULL COMMENT 'Incident start time',
  status ENUM('Active', 'Closed') NOT NULL DEFAULT 'Active',
  source ENUM('Manual', 'UptimeKuma', 'Other') COMMENT 'How incident was triggered',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_incident_plan (plan_id),
  INDEX idx_bcp_incident_status (status),
  INDEX idx_bcp_incident_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Checklist Item: Checklist items for incident response phases
CREATE TABLE IF NOT EXISTS bcp_checklist_item (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  phase ENUM('Immediate', 'CrisisRecovery') NOT NULL COMMENT 'Response phase',
  label VARCHAR(500) NOT NULL,
  default_order INT NOT NULL DEFAULT 0 COMMENT 'Display order',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_checklist_plan (plan_id),
  INDEX idx_bcp_checklist_phase (phase),
  INDEX idx_bcp_checklist_order (plan_id, phase, default_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Checklist Tick: Tracks completion of checklist items for specific incidents
CREATE TABLE IF NOT EXISTS bcp_checklist_tick (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  checklist_item_id INT NOT NULL,
  incident_id INT NOT NULL,
  is_done BOOLEAN NOT NULL DEFAULT FALSE COMMENT 'Completion status',
  done_at DATETIME COMMENT 'When completed',
  done_by INT COMMENT 'User ID who completed',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  FOREIGN KEY (checklist_item_id) REFERENCES bcp_checklist_item(id) ON DELETE CASCADE,
  FOREIGN KEY (incident_id) REFERENCES bcp_incident(id) ON DELETE CASCADE,
  INDEX idx_bcp_tick_incident (incident_id),
  INDEX idx_bcp_tick_item (checklist_item_id),
  INDEX idx_bcp_tick_plan (plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Evacuation Plan: Evacuation procedures and meeting points
CREATE TABLE IF NOT EXISTS bcp_evacuation_plan (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  meeting_point VARCHAR(500) COMMENT 'Primary meeting point location',
  floorplan_file_id INT COMMENT 'Reference to uploaded floorplan file',
  notes TEXT COMMENT 'Additional evacuation notes',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_evacuation_plan (plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Emergency Kit Item: Items in emergency preparedness kit
CREATE TABLE IF NOT EXISTS bcp_emergency_kit_item (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  category ENUM('Document', 'Equipment') NOT NULL COMMENT 'Item category',
  name VARCHAR(255) NOT NULL,
  notes TEXT,
  last_checked_at DATETIME COMMENT 'Last verification date',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_kit_plan (plan_id),
  INDEX idx_bcp_kit_category (category),
  INDEX idx_bcp_kit_checked (last_checked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Role: Emergency response roles and responsibilities
CREATE TABLE IF NOT EXISTS bcp_role (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  title VARCHAR(255) NOT NULL,
  responsibilities TEXT COMMENT 'Role responsibilities',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_role_plan (plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Role Assignment: Assigns users to emergency response roles
CREATE TABLE IF NOT EXISTS bcp_role_assignment (
  id INT AUTO_INCREMENT PRIMARY KEY,
  role_id INT NOT NULL,
  user_id INT NOT NULL COMMENT 'Assigned user',
  is_alternate BOOLEAN NOT NULL DEFAULT FALSE COMMENT 'Is this an alternate/backup?',
  contact_info VARCHAR(500) COMMENT 'Emergency contact information',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (role_id) REFERENCES bcp_role(id) ON DELETE CASCADE,
  INDEX idx_bcp_role_assignment_role (role_id),
  INDEX idx_bcp_role_assignment_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Contact: Emergency contacts (internal and external)
CREATE TABLE IF NOT EXISTS bcp_contact (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  kind ENUM('Internal', 'External') NOT NULL COMMENT 'Contact type',
  person_or_org VARCHAR(255) NOT NULL COMMENT 'Name of person or organization',
  phones VARCHAR(500) COMMENT 'Phone numbers (comma-separated)',
  email VARCHAR(255),
  responsibility_or_agency VARCHAR(500) COMMENT 'Role/responsibility or agency name',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_contact_plan (plan_id),
  INDEX idx_bcp_contact_kind (kind)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Event Log Entry: Chronological log of events during an incident
CREATE TABLE IF NOT EXISTS bcp_event_log_entry (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  incident_id INT,
  happened_at DATETIME NOT NULL COMMENT 'Event timestamp',
  author_id INT COMMENT 'User who logged this event',
  notes TEXT NOT NULL,
  initials VARCHAR(10) COMMENT 'Author initials for quick reference',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  FOREIGN KEY (incident_id) REFERENCES bcp_incident(id) ON DELETE CASCADE,
  INDEX idx_bcp_event_plan (plan_id),
  INDEX idx_bcp_event_incident (incident_id),
  INDEX idx_bcp_event_time (happened_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- Recovery
-- ============================================================================

-- BCP Recovery Action: Recovery actions linked to critical activities
CREATE TABLE IF NOT EXISTS bcp_recovery_action (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  critical_activity_id INT,
  action TEXT NOT NULL,
  resources TEXT COMMENT 'Required resources',
  owner_id INT COMMENT 'User responsible for action',
  rto_hours INT COMMENT 'Recovery time objective in hours',
  due_date DATETIME COMMENT 'Target completion date',
  completed_at DATETIME COMMENT 'Actual completion date',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  FOREIGN KEY (critical_activity_id) REFERENCES bcp_critical_activity(id) ON DELETE SET NULL,
  INDEX idx_bcp_recovery_plan (plan_id),
  INDEX idx_bcp_recovery_activity (critical_activity_id),
  INDEX idx_bcp_recovery_owner (owner_id),
  INDEX idx_bcp_recovery_due (due_date),
  CONSTRAINT ck_recovery_rto_positive CHECK (rto_hours >= 0 OR rto_hours IS NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Recovery Contact: External contacts for recovery assistance
CREATE TABLE IF NOT EXISTS bcp_recovery_contact (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  org_name VARCHAR(255) NOT NULL COMMENT 'Organization name',
  contact_name VARCHAR(255) COMMENT 'Contact person name',
  title VARCHAR(255) COMMENT 'Contact person title',
  phone VARCHAR(50),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_recovery_contact_plan (plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Insurance Claim: Insurance claims filed during/after incidents
CREATE TABLE IF NOT EXISTS bcp_insurance_claim (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  insurer VARCHAR(255) NOT NULL,
  claim_date DATETIME COMMENT 'Date claim was filed',
  details TEXT COMMENT 'Claim details',
  follow_up_actions TEXT COMMENT 'Follow-up actions needed',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_claim_plan (plan_id),
  INDEX idx_bcp_claim_date (claim_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Market Change: Market changes and strategic impacts on business continuity
CREATE TABLE IF NOT EXISTS bcp_market_change (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  `change` TEXT NOT NULL COMMENT 'Description of market change',
  impact TEXT COMMENT 'Impact on business continuity',
  options TEXT COMMENT 'Response options',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_market_plan (plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Training Item: Training sessions and exercises for BCP preparedness
CREATE TABLE IF NOT EXISTS bcp_training_item (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  training_date DATETIME NOT NULL COMMENT 'Training session date',
  training_type VARCHAR(255) COMMENT 'Type of training (e.g., Tabletop, Full-scale)',
  comments TEXT COMMENT 'Training notes and outcomes',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_training_plan (plan_id),
  INDEX idx_bcp_training_date (training_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Review Item: Plan review history and changes
CREATE TABLE IF NOT EXISTS bcp_review_item (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  review_date DATETIME NOT NULL COMMENT 'Date of review',
  reason TEXT COMMENT 'Reason for review',
  changes_made TEXT COMMENT 'Summary of changes made',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE,
  INDEX idx_bcp_review_plan (plan_id),
  INDEX idx_bcp_review_date (review_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
