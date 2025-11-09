-- Add billing fields to call_recordings table
ALTER TABLE call_recordings
  ADD COLUMN minutes_spent INT NULL AFTER duration_seconds,
  ADD COLUMN is_billable TINYINT(1) NOT NULL DEFAULT 0 AFTER minutes_spent,
  ADD COLUMN labour_type_id INT NULL AFTER is_billable,
  ADD FOREIGN KEY (labour_type_id) REFERENCES ticket_labour_types(id) ON DELETE SET NULL;

-- Add index for labour_type_id
ALTER TABLE call_recordings
  ADD INDEX idx_labour_type (labour_type_id);
