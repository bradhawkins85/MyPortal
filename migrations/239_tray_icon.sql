-- Tray icon override: optional admin-uploaded .ico file path stored
-- relative to the application's private uploads directory. When unset,
-- the tray app falls back to a default icon derived from the website
-- favicon palette.
ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS tray_icon_path VARCHAR(255) NULL DEFAULT NULL;
