-- phase: data
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Monthly subscription renewal reminders now go out 14 days before renewal and
-- invoices 8 days before. Update the seeded monthly template only when it still
-- holds the original default text, so admin-customised templates are kept.

UPDATE message_templates
SET content = '<p>Hi {{ recipient.name }},</p><p>This is your 14-day reminder that the monthly services listed below are due for renewal.</p><p>We will generate an invoice 8 days before the renewal date. Services with invoices that remain unpaid on their expiry date will not be renewed and may incur additional charges to reinstate their licences.</p>{{ renewal.items_table }}<p>Kind regards,<br>MyPortal Accounts Team</p>'
WHERE slug = 'monthly-subscription-renewal-reminder'
  AND content = '<p>Hi {{ recipient.name }},</p><p>This is your 30-day reminder that the monthly services listed below are due for renewal.</p><p>We will generate an invoice 21 days before the renewal date. Services with invoices that remain unpaid on their expiry date will not be renewed and may incur additional charges to reinstate their licences.</p>{{ renewal.items_table }}<p>Kind regards,<br>MyPortal Accounts Team</p>';

UPDATE message_templates
SET description = 'Sent 14 days before a monthly subscription renewal. Variables: {{ recipient.name }}, {{ company.name }}, {{ renewal.date }}, and {{ renewal.items_table }}. Keep {{ renewal.items_table }} to include renewal details and the total cost.'
WHERE slug = 'monthly-subscription-renewal-reminder'
  AND description = 'Sent 30 days before a monthly subscription renewal. Variables: {{ recipient.name }}, {{ company.name }}, {{ renewal.date }}, and {{ renewal.items_table }}. Keep {{ renewal.items_table }} to include renewal details and the total cost.';

UPDATE scheduled_tasks
SET description = 'Create annual subscription reminders/invoices inside 60/30 days and monthly subscription reminders/invoices inside 14/8 days for all companies'
WHERE command = 'process_subscription_renewals'
  AND description = 'Create annual subscription reminders/invoices inside 60/30 days and monthly subscription reminders/invoices inside 30/21 days for all companies';
