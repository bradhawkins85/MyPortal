-- Support externally invoiced subscriptions without forcing a Shop catalogue item.
ALTER TABLE subscriptions
    MODIFY COLUMN product_id INT NULL;
ALTER TABLE scheduled_invoice_lines
    MODIFY COLUMN product_id INT NULL;
ALTER TABLE subscriptions ADD COLUMN vendor VARCHAR(255) NULL AFTER product_id;
ALTER TABLE subscriptions ADD COLUMN external_name VARCHAR(255) NULL AFTER vendor;
ALTER TABLE subscriptions ADD COLUMN external_sku VARCHAR(255) NULL AFTER external_name;
ALTER TABLE subscriptions ADD COLUMN billing_frequency ENUM('annual', 'monthly') NULL AFTER external_sku;
ALTER TABLE subscriptions ADD COLUMN reminder_only TINYINT(1) NOT NULL DEFAULT 0 AFTER billing_frequency;

CREATE INDEX idx_subscriptions_customer_external_sku
    ON subscriptions(customer_id, external_sku);

INSERT IGNORE INTO message_templates (slug, name, description, content_type, content) VALUES
  (
    'third-party-annual-subscription-renewal-reminder',
    'Third-party annual subscription renewal reminder',
    'Sent before an annual subscription invoiced directly by an external vendor renews. Variables: {{ recipient.name }}, {{ company.name }}, {{ renewal.date }}, and {{ renewal.items_table }}.',
    'text/html',
    '<p>Hi {{ recipient.name }},</p><p>This is a reminder that the externally billed annual subscriptions below are due for renewal. Your vendor will invoice you directly; MyPortal will not issue an invoice.</p>{{ renewal.items_table }}<p>Kind regards,<br>MyPortal Accounts Team</p>'
  ),
  (
    'third-party-monthly-subscription-renewal-reminder',
    'Third-party monthly subscription renewal reminder',
    'Sent before a monthly subscription invoiced directly by an external vendor renews. Variables: {{ recipient.name }}, {{ company.name }}, {{ renewal.date }}, and {{ renewal.items_table }}.',
    'text/html',
    '<p>Hi {{ recipient.name }},</p><p>This is a reminder that the externally billed monthly subscriptions below are due for renewal. Your vendor will invoice you directly; MyPortal will not issue an invoice.</p>{{ renewal.items_table }}<p>Kind regards,<br>MyPortal Accounts Team</p>'
  );
