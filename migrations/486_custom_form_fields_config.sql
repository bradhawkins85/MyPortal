-- 486: Add a flexible per-field ``config`` column to custom forms.
--
-- Stores a JSON configuration blob for field types that need one
-- (e.g. matrix rows/columns, rating max value, audio max clips, signature
-- dimensions). Existing rows default to an empty object so the column is
-- always a valid JSON value after the change.
--
-- phase: expand

ALTER TABLE custom_form_fields ADD COLUMN config JSON NULL;