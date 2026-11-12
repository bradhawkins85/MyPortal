-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 554: Records whether the tray device's console_user is signed in
-- now (1) or is only the last user to sign in (0). NULL means the agent is too
-- old to report it, and is treated as signed in.
ALTER TABLE tray_devices ADD COLUMN IF NOT EXISTS console_user_active TINYINT(1) NULL;
