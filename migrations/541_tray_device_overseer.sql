-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 541: Record the overseer that keeps a device's tray service and
-- RMM agent connected. The overseer reports with the tray device's token, so
-- its version and last report live on the tray device. NULL means no overseer
-- has reported from the device yet (older tray releases do not have one).
ALTER TABLE tray_devices ADD COLUMN IF NOT EXISTS overseer_version VARCHAR(32) NULL;
ALTER TABLE tray_devices ADD COLUMN IF NOT EXISTS overseer_last_seen_utc DATETIME NULL;
