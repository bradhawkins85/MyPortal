-- Add booking_link_url column to users table for per-technician booking links
ALTER TABLE users ADD COLUMN IF NOT EXISTS booking_link_url VARCHAR(500);
