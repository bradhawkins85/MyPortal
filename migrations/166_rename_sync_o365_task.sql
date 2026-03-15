-- Migration 166: Rename legacy M365 scheduled task command and title.
UPDATE scheduled_tasks
SET command = 'sync_m365_data'
WHERE command = 'sync_o365';

UPDATE scheduled_tasks
SET name = 'Sync Microsoft 365 data'
WHERE command = 'sync_m365_data'
  AND name = 'Sync Microsoft 365 licenses';
