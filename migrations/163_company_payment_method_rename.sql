UPDATE companies SET payment_method = 'invoice_prepay' WHERE payment_method = 'invoice';
ALTER TABLE companies MODIFY COLUMN payment_method VARCHAR(100) NOT NULL DEFAULT 'invoice_prepay';
