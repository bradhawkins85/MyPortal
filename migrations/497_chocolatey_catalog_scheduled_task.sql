-- phase: data
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Create the default Chocolatey app list refresh. Administrators can edit,
-- disable or delete it from Scheduled Tasks; it is only seeded once.
INSERT INTO scheduled_tasks (name, command, cron, active, description, max_retries, retry_backoff_seconds)
SELECT
  'Update Chocolatey app list',
  'update_chocolatey_catalog',
  '23 4 * * *',
  1,
  'Download the stable Chocolatey community catalog used by patch policies and asset upgrade checks.',
  3,
  900
WHERE NOT EXISTS (
  SELECT 1 FROM scheduled_tasks WHERE command = 'update_chocolatey_catalog'
);
