-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 588: flag whether a user has changed a view preference (for
-- example a table's column layout) from the page's default. Rows already in
-- the table were saved by an explicit user change, so they default to 1; the
-- application always writes the flag explicitly, seeding untouched defaults
-- with 0 so future default-layout changes reach only users who never
-- customised that page.

ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS is_modified TINYINT(1) NOT NULL DEFAULT 1;
