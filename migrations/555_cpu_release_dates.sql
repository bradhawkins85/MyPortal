-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 555: CPU release dates for estimating device age.
--
-- cpu_release_dates holds one row per distinct processor model seen in the
-- portal (keyed by a normalised model string such as "intel:i7-8650u"), with
-- the launch date found for it. Intel processors are looked up on Intel ARK;
-- other processors can have their date recorded by hand (source 'manual').
-- assets.cpu_release_date copies the matched date onto each device, and
-- assets.approx_age is derived from it (whole years since the CPU launched).

CREATE TABLE IF NOT EXISTS cpu_release_dates (
  id INT AUTO_INCREMENT PRIMARY KEY,
  cpu_key VARCHAR(191) NOT NULL,
  cpu_name VARCHAR(255) NOT NULL,
  vendor VARCHAR(32) NULL,
  model VARCHAR(128) NULL,
  release_date DATE NULL,
  release_label VARCHAR(32) NULL,
  source VARCHAR(32) NULL,
  source_url VARCHAR(512) NULL,
  lookup_status VARCHAR(32) NULL,
  lookup_message VARCHAR(255) NULL,
  checked_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_cpu_release_dates_key UNIQUE (cpu_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_cpu_release_dates_checked ON cpu_release_dates (checked_at);

ALTER TABLE assets ADD COLUMN IF NOT EXISTS cpu_release_date DATE NULL;
