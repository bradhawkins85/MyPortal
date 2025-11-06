ALTER TABLE issue_definitions
  ADD COLUMN IF NOT EXISTS slug VARCHAR(255) NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_issue_definitions_slug 
  ON issue_definitions (slug)
  WHERE slug IS NOT NULL;
