-- Optional branded display name shown when users hover over the tray icon.
ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS tray_icon_tooltip_name VARCHAR(100) NULL DEFAULT NULL;
