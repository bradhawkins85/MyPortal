-- Migration 238: Seed default global tray menu configuration
-- Inserts a single global-scope tray menu config if none exists yet.
-- The menu items reflect the standard support contact layout described
-- in the tray app design document.
--
-- Idempotent: the INSERT only runs when no global-scope row is present.

INSERT INTO tray_menu_configs
  (name, scope, scope_ref_id, payload_json, display_text, env_allowlist,
   branding_icon_url, enabled, version, created_by_user_id, updated_by_user_id)
SELECT
  'Default Global Menu',
  'global',
  NULL,
  '[{"type":"label","label":"MyPortal"},{"type":"separator"},{"type":"label","label":"Contact Support"},{"type":"label","label":"Email: myportal@company.com.au"},{"type":"label","label":"Phone: 0755000000"},{"type":"separator"},{"type":"link","label":"Submit Ticket","url":"https://www.google.com"},{"type":"separator"},{"type":"link","label":"Knowledge Base","url":"/kb"},{"type":"separator"},{"type":"open_chat","label":"Chat"}]',
  NULL,
  NULL,
  NULL,
  1,
  1,
  NULL,
  NULL
WHERE NOT EXISTS (
  SELECT 1 FROM tray_menu_configs WHERE scope = 'global'
);
