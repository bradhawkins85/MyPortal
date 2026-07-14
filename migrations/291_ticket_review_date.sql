-- Add an optional technician review date to tickets.
ALTER TABLE tickets ADD COLUMN review_date DATE NULL;
CREATE INDEX idx_tickets_review_date ON tickets(review_date);
