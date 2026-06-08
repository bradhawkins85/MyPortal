-- Migration 260: Staged rollout support for tray installer versions.
--
-- Two new columns on tray_versions:
--
--   rollout_percent   INTEGER (1–100, default 100)
--       Percentage of the enrolled device fleet that should receive this
--       version.  The server deterministically assigns each device to a
--       bucket using crc32(device_uid) % 100; devices whose bucket is
--       >= rollout_percent are held back and served the previous stable
--       version until the rollout is widened.
--
--   rollout_start_at  DATETIME (optional)
--       Informational timestamp recording when the staged rollout began.
--       Not used by the version-selection logic directly but shown in the
--       admin UI to help operators track rollout age.

ALTER TABLE tray_versions
    ADD COLUMN IF NOT EXISTS rollout_percent INT NOT NULL DEFAULT 100;

ALTER TABLE tray_versions
    ADD COLUMN IF NOT EXISTS rollout_start_at DATETIME NULL;
