-- phase: data
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Show the Email field on the Add staff member form and make Mobile phone
-- optional. Company overrides for these two settings are cleared so every
-- company picks up the new defaults; admins can change them again per company.

UPDATE staff_field_definitions
SET default_visible = 1
WHERE field_key = 'email'
  AND default_visible != 1;

UPDATE staff_field_definitions
SET default_required = 0
WHERE field_key = 'mobile_phone'
  AND default_required != 0;

UPDATE company_staff_field_configs
SET visible = NULL
WHERE visible IS NOT NULL
  AND field_definition_id IN (
      SELECT id FROM staff_field_definitions WHERE field_key = 'email'
  );

UPDATE company_staff_field_configs
SET required = NULL
WHERE required IS NOT NULL
  AND field_definition_id IN (
      SELECT id FROM staff_field_definitions WHERE field_key = 'mobile_phone'
  );
