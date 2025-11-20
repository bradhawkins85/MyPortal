-- Add email_signature column to users table for helpdesk technician email signatures
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_signature TEXT;
