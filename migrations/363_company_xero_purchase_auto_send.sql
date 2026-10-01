ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS xero_auto_send_subscription_invoices TINYINT(1) NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS xero_auto_send_product_invoices TINYINT(1) NOT NULL DEFAULT 1;
