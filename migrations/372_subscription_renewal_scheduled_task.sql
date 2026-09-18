-- Make subscription renewal reminders and invoices administrator-schedulable.
-- This is a single global task because one run safely processes every company.
INSERT INTO scheduled_tasks
    (name, command, cron, active, description, max_retries, retry_backoff_seconds)
SELECT
    'Process subscription renewals',
    'process_subscription_renewals',
    '0 2 * * *',
    1,
    'Create subscription reminder tickets inside 60 days and renewal invoices inside 30 days for all companies',
    3,
    300
WHERE NOT EXISTS (
    SELECT 1
    FROM scheduled_tasks
    WHERE command = 'process_subscription_renewals'
);
