-- Add rate column to ticket_labour_types table for storing hourly billing rates
ALTER TABLE ticket_labour_types
    ADD COLUMN rate DECIMAL(10,2) NULL AFTER name;
