-- Migration 200: Update staff intake field defaults
-- 1. Remove the 'enabled' field from staff intake form definitions
-- 2. Default email and department to not visible
-- 3. Default mobile_phone to required
-- 4. Rename 'Onboard date' to 'Start Date'

DELETE FROM staff_field_definitions
WHERE field_key = 'enabled';

UPDATE staff_field_definitions
SET default_visible = 0
WHERE field_key IN ('email', 'department')
  AND default_visible != 0;

UPDATE staff_field_definitions
SET default_required = 1
WHERE field_key = 'mobile_phone'
  AND default_required != 1;

UPDATE staff_field_definitions
SET label = 'Start Date'
WHERE field_key = 'date_onboarded'
  AND label != 'Start Date';
