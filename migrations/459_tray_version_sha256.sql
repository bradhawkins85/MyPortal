-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 459: Record the SHA-256 digest of published tray installers.
-- Auto-updating tray devices verify the digest before executing
-- msiexec/installer, so a tampered or mis-published installer is never run.
-- NULL keeps legacy publishes working; the updater logs a warning when a
-- published version carries no digest.
ALTER TABLE tray_versions ADD COLUMN IF NOT EXISTS sha256 CHAR(64) NULL;
