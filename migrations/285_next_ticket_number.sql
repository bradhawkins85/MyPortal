ALTER TABLE site_settings
  ADD COLUMN IF NOT EXISTS next_ticket_number INT NULL;
