-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Device rules moved from whole tray menu configurations to individual menu
-- items (stored on each node in payload_json). A configuration that was saved
-- with menu-level rules would otherwise apply to every device in its scope,
-- so disable it; its rules stay in target_rules for the admin to move onto
-- items. The target_rules and priority columns are no longer read.
UPDATE tray_menu_configs SET enabled = 0
WHERE target_rules IS NOT NULL AND target_rules <> '' AND enabled = 1;
