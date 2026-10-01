-- Voice Monitor is usable immediately after the feature migrations run.  The
-- plan records its call allowance explicitly instead of inferring it from a
-- normal shop item's stock or description.
INSERT INTO subscription_categories (name, description)
VALUES ('Voice Monitor', 'Voice Monitor plans priced per scheduled check/call.')
ON DUPLICATE KEY UPDATE description = VALUES(description);

ALTER TABLE shop_products
  ADD COLUMN IF NOT EXISTS voice_monitor_calls_per_day TINYINT UNSIGNED NULL
  COMMENT 'Scheduled checks per day (1-24) for Voice Monitor subscription products';

INSERT INTO shop_products
  (name, sku, vendor_sku, description, price, stock, archived,
   subscription_category_id, commitment_type, payment_frequency,
   price_monthly_commitment, voice_monitor_calls_per_day)
SELECT 'Voice Monitor - 1 call/day', 'VOICE-MONITOR-1', 'VOICE-MONITOR-1',
       'One scheduled Voice Monitor check per day ($2 per call).',
       2.00, 999999, 0, c.id, 'monthly', 'monthly', 2.00, 1
FROM subscription_categories c
WHERE c.name = 'Voice Monitor'
  AND NOT EXISTS (SELECT 1 FROM shop_products p WHERE p.sku = 'VOICE-MONITOR-1');

INSERT INTO shop_products
  (name, sku, vendor_sku, description, price, stock, archived,
   subscription_category_id, commitment_type, payment_frequency,
   price_monthly_commitment, voice_monitor_calls_per_day)
SELECT 'Voice Monitor - 12 calls/day', 'VOICE-MONITOR-12', 'VOICE-MONITOR-12',
       'Twelve scheduled Voice Monitor checks per day ($15 per day).',
       15.00, 999999, 0, c.id, 'monthly', 'monthly', 15.00, 12
FROM subscription_categories c
WHERE c.name = 'Voice Monitor'
  AND NOT EXISTS (SELECT 1 FROM shop_products p WHERE p.sku = 'VOICE-MONITOR-12');
