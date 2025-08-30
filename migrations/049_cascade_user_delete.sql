ALTER TABLE user_companies
  DROP FOREIGN KEY user_companies_ibfk_1,
  ADD CONSTRAINT fk_user_companies_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE order_sms_subscriptions
  DROP FOREIGN KEY order_sms_subscriptions_ibfk_1,
  ADD CONSTRAINT fk_order_sms_subscriptions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
