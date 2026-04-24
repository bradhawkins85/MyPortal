-- Add per-section "detailed" flag to company_report_sections.
-- When detailed = 1 the report will append an extra detail page for that
-- section after the summary view (both web and PDF).
ALTER TABLE company_report_sections
    ADD COLUMN detailed TINYINT(1) NOT NULL DEFAULT 0;
