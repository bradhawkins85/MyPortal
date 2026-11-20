-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Marks subscription products that are priced per user, so the store shows
-- prices as /user/month or /user/year.
ALTER TABLE shop_products
  ADD COLUMN IF NOT EXISTS subscription_per_user TINYINT(1) NOT NULL DEFAULT 0;
