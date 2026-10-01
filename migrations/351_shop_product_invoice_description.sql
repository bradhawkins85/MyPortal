ALTER TABLE shop_products
    ADD COLUMN IF NOT EXISTS invoice_description TEXT NULL AFTER description;
