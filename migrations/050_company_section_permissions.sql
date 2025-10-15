ALTER TABLE user_companies
  ADD COLUMN IF NOT EXISTS can_access_cart TINYINT(1),
  ADD COLUMN IF NOT EXISTS can_access_orders TINYINT(1),
  ADD COLUMN IF NOT EXISTS can_access_forms TINYINT(1);

UPDATE user_companies
SET
  can_access_cart = IFNULL(can_access_cart, can_access_shop),
  can_access_orders = IFNULL(can_access_orders, can_access_shop),
  can_access_forms = IFNULL(can_access_forms, 1)
WHERE
  can_access_cart IS NULL
  OR can_access_orders IS NULL
  OR can_access_forms IS NULL;

ALTER TABLE user_companies
  MODIFY can_access_cart TINYINT(1) DEFAULT 0 NOT NULL,
  MODIFY can_access_orders TINYINT(1) DEFAULT 0 NOT NULL,
  MODIFY can_access_forms TINYINT(1) DEFAULT 0 NOT NULL;
