-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Asset inventory is internal unless an administrator deliberately publishes it.
ALTER TABLE assets
    ADD COLUMN customer_visible TINYINT(1) NOT NULL DEFAULT 0;
