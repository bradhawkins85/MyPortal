-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 575: each RMM printer's queued job count (Get-Printer JobCount) and
-- the PrinterStatus value exactly as the device reported it. rmm_printers.status
-- keeps the plain-language status shown on the asset page.

ALTER TABLE rmm_printers ADD COLUMN IF NOT EXISTS job_count INT NULL;
ALTER TABLE rmm_printers ADD COLUMN IF NOT EXISTS printer_status VARCHAR(64) NULL;
