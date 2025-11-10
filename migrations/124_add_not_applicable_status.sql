-- Add 'not_applicable' to the status ENUM in existing tables

-- Update company_essential8_compliance table
ALTER TABLE company_essential8_compliance 
MODIFY COLUMN status ENUM('not_started', 'in_progress', 'compliant', 'non_compliant', 'not_applicable') DEFAULT 'not_started';

-- Update company_essential8_audit table
ALTER TABLE company_essential8_audit 
MODIFY COLUMN old_status ENUM('not_started', 'in_progress', 'compliant', 'non_compliant', 'not_applicable');

ALTER TABLE company_essential8_audit 
MODIFY COLUMN new_status ENUM('not_started', 'in_progress', 'compliant', 'non_compliant', 'not_applicable');
