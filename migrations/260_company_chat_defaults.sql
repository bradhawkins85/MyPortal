-- Migration 260: Company chat defaults
-- Adds a company-wide customer chat toggle. The default is enabled so new
-- companies have customer chat available unless an admin disables it.

ALTER TABLE companies ADD COLUMN IF NOT EXISTS customer_chat_enabled TINYINT(1) NOT NULL DEFAULT 1;
