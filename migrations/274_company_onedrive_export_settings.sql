-- Migration 274: per-company OneDrive export destination settings.
-- Stores the SharePoint site/default document-library drive selected by admins
-- for manual Staff table OneDrive exports and workflow fallback behaviour.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS onedrive_export_site_id VARCHAR(512) NULL,
    ADD COLUMN IF NOT EXISTS onedrive_export_site_name VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS onedrive_export_drive_id VARCHAR(512) NULL;
