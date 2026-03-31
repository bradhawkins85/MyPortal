-- Migration 185: Make email intake field optional by default

UPDATE staff_field_definitions
SET default_required = 0
WHERE field_key = 'email'
  AND default_required != 0;
