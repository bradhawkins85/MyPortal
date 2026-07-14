-- Migration 298: Enable company chat and desktop notification defaults
-- Ensure new and existing companies have chat and desktop push notifications
-- enabled unless an admin disables them after this migration.

ALTER TABLE companies ADD COLUMN IF NOT EXISTS customer_chat_enabled TINYINT(1) NOT NULL DEFAULT 1;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS tray_chat_enabled TINYINT(1) NOT NULL DEFAULT 1;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS tray_notifications_enabled TINYINT(1) NOT NULL DEFAULT 1;

UPDATE companies
SET customer_chat_enabled = 1,
    tray_chat_enabled = 1,
    tray_notifications_enabled = 1
WHERE customer_chat_enabled IS NULL
   OR customer_chat_enabled <> 1
   OR tray_chat_enabled IS NULL
   OR tray_chat_enabled <> 1
   OR tray_notifications_enabled IS NULL
   OR tray_notifications_enabled <> 1;

ALTER TABLE companies MODIFY COLUMN customer_chat_enabled TINYINT(1) NOT NULL DEFAULT 1;
ALTER TABLE companies MODIFY COLUMN tray_chat_enabled TINYINT(1) NOT NULL DEFAULT 1;
ALTER TABLE companies MODIFY COLUMN tray_notifications_enabled TINYINT(1) NOT NULL DEFAULT 1;
