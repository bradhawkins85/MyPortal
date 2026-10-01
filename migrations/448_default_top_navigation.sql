-- Top menu is now the default navigation style. Move every user with a saved
-- menu layout onto it; users can still switch back to the left menu from
-- their profile.
UPDATE user_sidebar_preferences
SET preferences_json = JSON_SET(preferences_json, '$.navigation_style', 'top');
