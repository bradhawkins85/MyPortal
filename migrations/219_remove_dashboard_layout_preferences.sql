-- The customisable dashboard layout system has been removed in favour of an
-- opinionated, server-rendered dashboard. Purge the previously stored per-user
-- layout JSON so abandoned rows do not linger in user_preferences.
--
-- Idempotent: a second run simply deletes nothing.
DELETE FROM user_preferences WHERE preference_key = 'dashboard:layout:v1';
