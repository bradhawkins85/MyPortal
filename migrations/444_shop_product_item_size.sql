-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Freight item size override for shop products. NULL means the size is
-- derived from the stock-feed dimensions; 'digital' excludes the item from
-- freight because it is not shipped as a physical product.
ALTER TABLE shop_products
  ADD COLUMN IF NOT EXISTS item_size VARCHAR(32) NULL AFTER height;
