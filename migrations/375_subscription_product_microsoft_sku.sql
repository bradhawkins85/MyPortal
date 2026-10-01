-- Link subscription catalogue products to Microsoft licence assignment data.
ALTER TABLE shop_products
  ADD COLUMN IF NOT EXISTS microsoft_sku VARCHAR(255) NULL AFTER vendor_sku;

