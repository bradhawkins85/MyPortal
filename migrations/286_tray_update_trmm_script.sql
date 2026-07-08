ALTER TABLE site_settings
    ADD COLUMN IF NOT EXISTS tray_update_trmm_script_id INT NULL DEFAULT NULL;
