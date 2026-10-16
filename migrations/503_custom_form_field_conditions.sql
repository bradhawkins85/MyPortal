-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Conditional logic: {"match": "all"|"any", "rules": [{"field_id", "operator",
-- "value"}]}. NULL means the field is always shown.
ALTER TABLE custom_form_fields ADD COLUMN conditions JSON NULL;
