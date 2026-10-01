-- Make subscription renewal reminder wording and signature editable by global administrators.
INSERT IGNORE INTO message_templates (slug, name, description, content_type, content) VALUES
  (
    'subscription-renewal-reminder',
    'Subscription renewal reminder',
    'Sent 60 days before subscription renewal. Variables: {{ recipient.name }}, {{ company.name }}, {{ renewal.date }}, and {{ renewal.items_table }}. Keep {{ renewal.items_table }} to include renewal details and the total cost.',
    'text/html',
    '<p>Hi {{ recipient.name }},</p><p>This is your 60-day reminder that the services listed below are due for renewal.</p><p>We will generate an invoice 30 days before the renewal date. Services with invoices that remain unpaid on their expiry date will not be renewed and may incur additional charges to reinstate their licences.</p>{{ renewal.items_table }}<p>Kind regards,<br>MyPortal Accounts Team</p>'
  );
