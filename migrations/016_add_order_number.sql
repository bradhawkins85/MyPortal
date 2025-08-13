ALTER TABLE shop_orders
  ADD COLUMN order_number VARCHAR(20);

UPDATE shop_orders
SET order_number = CONCAT('TBC', LPAD(id, 12, '0'))
WHERE order_number IS NULL;
