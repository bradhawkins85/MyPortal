-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 456: Target signature templates at specific staff (by email,
-- job title, department, site/location or custom field) and let a template
-- be an additional signature that is available to staff without ever being
-- chosen as their primary signature.
ALTER TABLE m365_signature_templates
    ADD COLUMN IF NOT EXISTS signature_role VARCHAR(20) NOT NULL DEFAULT 'primary',
    ADD COLUMN IF NOT EXISTS targeting_match VARCHAR(10) NOT NULL DEFAULT 'all',
    ADD COLUMN IF NOT EXISTS targeting_rules LONGTEXT NULL;
