-- Improve shop list_products/list_products_summary/count_products performance.
-- Adds covering indexes for archived/category/stock filters, exclusion lookups,
-- and a FULLTEXT index for product search fields.

CREATE INDEX IF NOT EXISTS idx_shop_products_archived_category_name
    ON shop_products (archived, category_id, name);

CREATE INDEX IF NOT EXISTS idx_shop_products_archived_category_stock_name
    ON shop_products (archived, category_id, stock, name);

CREATE INDEX IF NOT EXISTS idx_shop_product_exclusions_company_product
    ON shop_product_exclusions (company_id, product_id);

SET @shop_product_fulltext_exists = (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'shop_products'
      AND INDEX_NAME = 'idx_shop_products_fulltext_search'
);

SET @sql = IF(
    @shop_product_fulltext_exists = 0,
    'ALTER TABLE shop_products ADD FULLTEXT INDEX idx_shop_products_fulltext_search (name, sku, vendor_sku)',
    'SELECT "Index idx_shop_products_fulltext_search already exists" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
