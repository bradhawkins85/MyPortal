-- Consolidate caller_number and callee_number into a single phone_number field
-- Since recordings only have one phone number, not separate caller/callee

-- Add the new phone_number column
ALTER TABLE call_recordings
  ADD COLUMN phone_number VARCHAR(50) AFTER file_name;

-- Migrate existing data - prefer caller_number, fallback to callee_number
UPDATE call_recordings
SET phone_number = COALESCE(caller_number, callee_number)
WHERE phone_number IS NULL;

-- Drop the old columns
ALTER TABLE call_recordings
  DROP COLUMN caller_number,
  DROP COLUMN callee_number;

-- Add index for phone_number
ALTER TABLE call_recordings
  ADD INDEX idx_phone_number (phone_number);
