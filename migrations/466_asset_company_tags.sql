-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 466: Tags for assets and companies.
--
-- tags is one shared list of labels used to target assets and companies in
-- automations and script filters. slug is the case- and spacing-folded name,
-- so "Server" and " server " are the same tag. auto_key marks a tag MyPortal
-- assigns automatically (for example "server" or "os_windows"); the rule keeps
-- working when the tag is renamed. asset_tags.source is 'auto' for a tag a rule
-- assigned and 'manual' for one a technician picked; rules only add and remove
-- their own rows.

CREATE TABLE IF NOT EXISTS tags (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(64) NOT NULL,
  slug VARCHAR(64) NOT NULL,
  colour VARCHAR(7) NULL,
  description VARCHAR(255) NULL,
  auto_key VARCHAR(64) NULL,
  created_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_tags_slug UNIQUE (slug),
  CONSTRAINT uq_tags_auto_key UNIQUE (auto_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS asset_tags (
  asset_id INT NOT NULL,
  tag_id INT NOT NULL,
  source VARCHAR(8) NOT NULL DEFAULT 'manual',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (asset_id, tag_id),
  CONSTRAINT fk_asset_tags_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
  CONSTRAINT fk_asset_tags_tag FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_asset_tags_tag ON asset_tags (tag_id);

CREATE TABLE IF NOT EXISTS company_tags (
  company_id INT NOT NULL,
  tag_id INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (company_id, tag_id),
  CONSTRAINT fk_company_tags_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_company_tags_tag FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_company_tags_tag ON company_tags (tag_id);
