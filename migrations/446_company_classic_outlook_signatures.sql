-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Per-company opt-in for the tray agent to write the published MyPortal
-- signature into Classic Outlook on managed Windows devices.
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS classic_outlook_signatures_enabled TINYINT(1) NOT NULL DEFAULT 0;
