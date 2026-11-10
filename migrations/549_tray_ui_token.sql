-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 549: Give the tray UI its own low-privilege token. The UI runs as
-- the signed-in user, so it no longer reads the device token; it gets a token
-- that only opens chat, the ticket form, Outlook signatures and menu scripts.
-- NULL means the device has not asked for a UI token yet (older releases).
ALTER TABLE tray_devices ADD COLUMN IF NOT EXISTS ui_token_hash VARCHAR(128) NULL;
ALTER TABLE tray_devices ADD COLUMN IF NOT EXISTS ui_token_prefix VARCHAR(16) NULL;
CREATE INDEX IF NOT EXISTS idx_tray_devices_ui_token_hash ON tray_devices (ui_token_hash);
