-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Keep the reporting stat strip aligned with the interactive M365 Best
-- Practices page. Results contain one current row per company/check, and rows
-- can have different run_at values when an individual check is evaluated.
UPDATE reporting_queries
SET sql_query = 'SELECT (SELECT secure_score_percentage FROM m365_best_practice_daily_history WHERE company_id = {{current.company}} ORDER BY snapshot_date DESC LIMIT 1) AS secure_score_percentage, COUNT(r.check_id) AS checks, COALESCE(SUM(CASE WHEN r.status = ''pass'' THEN 1 ELSE 0 END), 0) AS passed, COALESCE(SUM(CASE WHEN r.status = ''fail'' THEN 1 ELSE 0 END), 0) AS failed, COALESCE(SUM(CASE WHEN r.status = ''unknown'' THEN 1 ELSE 0 END), 0) AS unknown, COALESCE(SUM(CASE WHEN r.status = ''not_applicable'' THEN 1 ELSE 0 END), 0) AS not_applicable FROM m365_best_practice_results r LEFT JOIN m365_best_practice_settings s ON s.check_id = r.check_id LEFT JOIN m365_best_practice_company_exclusions e ON e.company_id = r.company_id AND e.check_id = r.check_id WHERE r.company_id = {{current.company}} AND (s.enabled = 1 OR s.check_id IS NULL) AND e.check_id IS NULL'
WHERE slug = 'stat-strip-m365-best-practices-live';
