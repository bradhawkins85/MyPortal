-- Migration 184: Unified staff fields
-- 1. Allow per-company field type overrides on intake fields
-- 2. Make staff.email nullable so it can be optional per company config

ALTER TABLE company_staff_field_configs
ADD COLUMN IF NOT EXISTS field_type VARCHAR(20) NULL;

ALTER TABLE staff
MODIFY COLUMN email VARCHAR(255) NULL;
