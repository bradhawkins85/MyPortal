-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- A stable identity makes asset creation retryable across workers and restarts.
ALTER TABLE assets ADD COLUMN tray_device_uid VARCHAR(64) NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_tray_device_uid ON assets (tray_device_uid);
