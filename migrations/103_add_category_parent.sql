ALTER TABLE shop_categories ADD COLUMN IF NOT EXISTS parent_id INT NULL;
ALTER TABLE shop_categories ADD COLUMN IF NOT EXISTS display_order INT NOT NULL DEFAULT 0;
ALTER TABLE shop_categories ADD CONSTRAINT fk_shop_categories_parent FOREIGN KEY (parent_id) REFERENCES shop_categories(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_shop_categories_parent ON shop_categories(parent_id);
