-- Track annual and monthly renewal runs independently when a company has both
-- subscription types ending on the same date.
ALTER TABLE scheduled_invoices
    ADD COLUMN renewal_cycle ENUM('annual', 'monthly') NOT NULL DEFAULT 'annual' AFTER scheduled_for_date;

ALTER TABLE scheduled_invoices
    DROP INDEX unique_customer_scheduled_date,
    ADD UNIQUE KEY unique_customer_date_cycle (
        customer_id, scheduled_for_date, renewal_cycle
    );

INSERT IGNORE INTO message_templates (slug, name, description, content_type, content) VALUES
  (
    'monthly-subscription-renewal-reminder',
    'Monthly subscription renewal reminder',
    'Sent 30 days before a monthly subscription renewal. Variables: {{ recipient.name }}, {{ company.name }}, {{ renewal.date }}, and {{ renewal.items_table }}. Keep {{ renewal.items_table }} to include renewal details and the total cost.',
    'text/html',
    '<p>Hi {{ recipient.name }},</p><p>This is your 30-day reminder that the monthly services listed below are due for renewal.</p><p>We will generate an invoice 21 days before the renewal date. Services with invoices that remain unpaid on their expiry date will not be renewed and may incur additional charges to reinstate their licences.</p>{{ renewal.items_table }}<p>Kind regards,<br>MyPortal Accounts Team</p>'
  );

UPDATE scheduled_tasks
SET description = 'Create annual subscription reminders/invoices inside 60/30 days and monthly subscription reminders/invoices inside 30/21 days for all companies'
WHERE command = 'process_subscription_renewals';
