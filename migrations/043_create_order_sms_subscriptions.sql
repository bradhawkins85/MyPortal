CREATE TABLE IF NOT EXISTS order_sms_subscriptions (
  order_number VARCHAR(20) NOT NULL,
  user_id INT NOT NULL,
  PRIMARY KEY (order_number, user_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
