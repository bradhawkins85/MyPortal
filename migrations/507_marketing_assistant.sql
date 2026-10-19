-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 507: Marketing Assistant.
--
-- The Marketing Assistant writes marketing page sections from websites an
-- admin lists for the page (marketing_assistant_sources). Every statement it
-- writes must be confirmed by those pages; images come only from the same
-- pages and are stored in marketing_assistant_images so the public page
-- serves them itself. marketing_assistant_runs records each run, the
-- statements it dropped because they could not be verified, and a snapshot
-- of the page before the run so the change can be undone.
--
-- Sections the assistant wrote are flagged assistant_generated; a later run
-- replaces only those and leaves hand-written sections alone.

CREATE TABLE IF NOT EXISTS marketing_assistant_sources (
  id INT AUTO_INCREMENT PRIMARY KEY,
  page_id INT NOT NULL,
  url VARCHAR(2048) NOT NULL,
  label VARCHAR(160) NULL,
  last_fetched_at DATETIME NULL,
  last_status VARCHAR(16) NULL,
  last_error VARCHAR(512) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_marketing_assistant_sources_page FOREIGN KEY (page_id) REFERENCES marketing_pages(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_marketing_assistant_sources_page ON marketing_assistant_sources (page_id, id);

CREATE TABLE IF NOT EXISTS marketing_assistant_runs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  page_id INT NOT NULL,
  mode VARCHAR(16) NOT NULL DEFAULT 'generate',
  status VARCHAR(16) NOT NULL DEFAULT 'running',
  instructions TEXT NULL,
  requested_by_user_id INT NULL,
  model VARCHAR(255) NULL,
  started_at DATETIME NOT NULL,
  finished_at DATETIME NULL,
  error VARCHAR(512) NULL,
  sources_read INT NOT NULL DEFAULT 0,
  sections_written INT NOT NULL DEFAULT 0,
  images_used INT NOT NULL DEFAULT 0,
  claims_verified INT NOT NULL DEFAULT 0,
  claims_rejected INT NOT NULL DEFAULT 0,
  report_json MEDIUMTEXT NULL,
  snapshot_json MEDIUMTEXT NULL,
  restored_at DATETIME NULL,
  CONSTRAINT fk_marketing_assistant_runs_page FOREIGN KEY (page_id) REFERENCES marketing_pages(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_marketing_assistant_runs_page ON marketing_assistant_runs (page_id, started_at);

CREATE TABLE IF NOT EXISTS marketing_assistant_images (
  id INT AUTO_INCREMENT PRIMARY KEY,
  page_id INT NOT NULL,
  sha256 CHAR(64) NOT NULL,
  source_page_url VARCHAR(2048) NOT NULL,
  image_url VARCHAR(2048) NOT NULL,
  content_type VARCHAR(64) NOT NULL,
  byte_size INT NOT NULL DEFAULT 0,
  data MEDIUMBLOB NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_marketing_assistant_images_page FOREIGN KEY (page_id) REFERENCES marketing_pages(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_marketing_assistant_images_page ON marketing_assistant_images (page_id, sha256);

ALTER TABLE marketing_page_sections ADD COLUMN IF NOT EXISTS image_id INT NULL;
ALTER TABLE marketing_page_sections ADD COLUMN IF NOT EXISTS image_alt VARCHAR(255) NULL;
ALTER TABLE marketing_page_sections ADD COLUMN IF NOT EXISTS image_caption VARCHAR(255) NULL;
ALTER TABLE marketing_page_sections ADD COLUMN IF NOT EXISTS source_urls_json TEXT NULL;
ALTER TABLE marketing_page_sections ADD COLUMN IF NOT EXISTS assistant_generated TINYINT(1) NOT NULL DEFAULT 0;
