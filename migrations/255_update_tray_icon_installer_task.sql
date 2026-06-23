-- Add scheduled task for keeping the tray icon installer current from GitHub Releases.
INSERT INTO scheduled_tasks (name, command, cron, active, description, max_retries, retry_backoff_seconds)
SELECT
  'Update Tray Icon Installer',
  'update_tray_icon_installer',
  '*/30 * * * *',
  1,
  'Download the latest MyPortal tray app MSI from GitHub Releases so clients receive the newest tray app without requiring a server restart.',
  12,
  300
WHERE NOT EXISTS (
  SELECT 1 FROM scheduled_tasks WHERE command = 'update_tray_icon_installer'
);
