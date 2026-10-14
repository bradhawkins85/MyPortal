-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Public forms are visible to every portal user without a per-user assignment.
ALTER TABLE forms
    ADD COLUMN is_public TINYINT(1) NOT NULL DEFAULT 0;
