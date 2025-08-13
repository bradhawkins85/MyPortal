CREATE TABLE IF NOT EXISTS shop_product_exclusions (
  product_id INT NOT NULL,
  company_id INT NOT NULL,
  PRIMARY KEY (product_id, company_id),
  FOREIGN KEY (product_id) REFERENCES shop_products(id) ON DELETE CASCADE,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);
