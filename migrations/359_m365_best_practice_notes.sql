ALTER TABLE m365_best_practice_results
    ADD COLUMN notes TEXT NULL;

UPDATE reporting_queries
SET sql_query = 'SELECT r.check_id, r.check_name, r.status, r.details, r.notes, r.remediation_status, r.run_at FROM m365_best_practice_results r WHERE r.company_id = {{current.company}} ORDER BY CASE r.status WHEN ''fail'' THEN 0 WHEN ''warn'' THEN 1 WHEN ''error'' THEN 2 WHEN ''pass'' THEN 3 ELSE 4 END, r.check_name ASC'
WHERE slug = 'report-m365-best-practice-summary'
  AND is_system = 1;
