-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 546: store a landline / direct-line number on staff records so a
-- technician can record it from the ticket "Add phone" dialog alongside the
-- existing mobile number.
ALTER TABLE staff ADD COLUMN IF NOT EXISTS landline_phone VARCHAR(32) NULL;
