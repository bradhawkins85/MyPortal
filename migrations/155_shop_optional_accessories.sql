CREATE TABLE IF NOT EXISTS shop_optional_accessories (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sku VARCHAR(255) NOT NULL,
    product_name VARCHAR(255),
    category_name VARCHAR(255),
    rrp DECIMAL(10, 2),
    image_url VARCHAR(255),
    manufacturer VARCHAR(255),
    referenced_by_skus TEXT,
    discovered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_shop_optional_accessories_sku (sku)
);
