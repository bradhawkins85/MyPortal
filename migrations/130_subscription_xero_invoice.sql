-- Add Xero invoice tracking to subscription change requests
ALTER TABLE subscription_change_requests
    ADD COLUMN xero_invoice_number VARCHAR(64) NULL AFTER prorated_charge;

CREATE INDEX IF NOT EXISTS idx_subscription_change_requests_xero_invoice 
    ON subscription_change_requests(xero_invoice_number);
