-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Tray menu configurations can carry advanced device rules (the same rule
-- trees RMM checks use). A device takes the first enabled rule-targeted menu
-- whose scope and rules match it, lowest priority first, before falling back
-- to the untargeted tag > company > global menus. NULL rules keep the
-- existing scope-only behaviour.
ALTER TABLE tray_menu_configs ADD COLUMN IF NOT EXISTS target_rules LONGTEXT NULL;
ALTER TABLE tray_menu_configs ADD COLUMN IF NOT EXISTS priority INT NOT NULL DEFAULT 100;
