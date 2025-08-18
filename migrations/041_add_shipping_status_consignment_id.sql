ALTER TABLE shop_orders
  ADD COLUMN shipping_status VARCHAR(50) NOT NULL DEFAULT 'pending',
  ADD COLUMN consignment_id VARCHAR(100);
