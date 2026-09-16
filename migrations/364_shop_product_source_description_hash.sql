ALTER TABLE shop_products
    ADD COLUMN IF NOT EXISTS source_description_hash CHAR(40) NULL AFTER description;
