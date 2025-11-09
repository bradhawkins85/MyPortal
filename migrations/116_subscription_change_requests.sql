-- Create subscription_change_requests table to track pending subscription changes
-- This enables stacking of license additions and decreases with prorata calculations
CREATE TABLE IF NOT EXISTS subscription_change_requests (
  id CHAR(36) PRIMARY KEY,
  subscription_id CHAR(36) NOT NULL,
  change_type ENUM('addition', 'decrease') NOT NULL,
  quantity_change INT NOT NULL,
  requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  requested_by INT NULL,
  status ENUM('pending', 'applied', 'cancelled') NOT NULL DEFAULT 'pending',
  applied_at TIMESTAMP NULL,
  prorated_charge DECIMAL(10,2) NULL,
  notes TEXT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE,
  FOREIGN KEY (requested_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_subscription_change_requests_subscription ON subscription_change_requests(subscription_id);
CREATE INDEX IF NOT EXISTS idx_subscription_change_requests_status ON subscription_change_requests(status);
CREATE INDEX IF NOT EXISTS idx_subscription_change_requests_requested_at ON subscription_change_requests(requested_at);
