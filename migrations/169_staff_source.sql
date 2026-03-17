ALTER TABLE staff ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'manual';
UPDATE staff SET source = 'syncro' WHERE syncro_contact_id IS NOT NULL AND source = 'manual';
