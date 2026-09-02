-- A provider's report_id is the tenant-scoped identity of an aggregate report.
-- Keep the newest copy of historical duplicates before enforcing that identity.
ALTER TABLE dmarc_reports ADD COLUMN IF NOT EXISTS nonexistent_policy VARCHAR(16) NULL AFTER subdomain_policy;

DELETE older FROM dmarc_reports older
JOIN dmarc_reports newer ON newer.company_id = older.company_id
  AND newer.report_id = older.report_id AND newer.id > older.id;

ALTER TABLE dmarc_reports DROP INDEX uq_dmarc_report;
CREATE UNIQUE INDEX uq_dmarc_report_id ON dmarc_reports (company_id, report_id);
