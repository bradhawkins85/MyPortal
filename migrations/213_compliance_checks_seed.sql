-- Seed predefined GMP and GLP compliance checks
-- All inserts use INSERT IGNORE on the unique code column so re-running is safe.

-- ============================================================
-- GMP (Good Manufacturing Practices) checks
-- ============================================================
INSERT IGNORE INTO compliance_checks
  (category_id, code, title, description, guidance, default_review_interval_days, default_evidence_required, is_predefined, is_active, sort_order)
SELECT
  c.id,
  checks.code,
  checks.title,
  checks.description,
  checks.guidance,
  checks.interval_days,
  1,
  1,
  1,
  checks.sort_order
FROM compliance_check_categories c
JOIN (
  SELECT
    'GMP-001'                                          AS code,
    'Personnel Hygiene & Training Records'             AS title,
    'Maintain up-to-date records of personnel hygiene training and health screening.' AS description,
    'Verify training logs are current, signed, and filed. Confirm health screening records are accessible for audit.' AS guidance,
    365 AS interval_days,
    1   AS sort_order
  UNION ALL SELECT 'GMP-002', 'Premises & Equipment Cleanliness Logs',
    'Document scheduled and completed cleaning of premises and equipment.',
    'Review cleaning schedules and completion records. Confirm no overdue items. Check that cleaning agents are appropriate for the environment.',
    90, 2
  UNION ALL SELECT 'GMP-003', 'Documented SOPs & Version Control',
    'All Standard Operating Procedures are documented, version-controlled, and accessible to relevant personnel.',
    'Confirm SOP index is current. Verify version numbers match distributed copies. Ensure superseded versions are archived.',
    180, 3
  UNION ALL SELECT 'GMP-004', 'Raw Material Identity & Supplier Qualification',
    'Incoming raw materials are identified, tested, and sourced from qualified suppliers.',
    'Check certificate of analysis for recent batches. Review approved supplier list currency. Confirm identity testing is performed on receipt.',
    365, 4
  UNION ALL SELECT 'GMP-005', 'Batch/Production Records',
    'Complete and accurate batch records are maintained for every production run.',
    'Sample recent batch records for completeness: all steps signed, deviations documented, yields recorded.',
    90, 5
  UNION ALL SELECT 'GMP-006', 'Quality Control Sampling & Retention',
    'QC samples are collected at defined intervals and retention samples are stored appropriately.',
    'Verify sampling frequency matches the approved protocol. Inspect retention sample storage conditions and labelling.',
    180, 6
  UNION ALL SELECT 'GMP-007', 'Complaints & Recalls Procedure',
    'A documented procedure exists for handling product complaints and initiating recalls.',
    'Review the complaints/recalls SOP for currency. Confirm responsible roles are assigned. Check that recent complaints were handled per procedure.',
    365, 7
  UNION ALL SELECT 'GMP-008', 'Internal Audits',
    'Internal GMP audits are conducted at planned intervals with findings tracked to closure.',
    'Confirm the audit schedule is current. Review most recent audit report. Verify all non-conformances have assigned owners and closure dates.',
    365, 8
  UNION ALL SELECT 'GMP-009', 'Pest Control Programme',
    'An active pest control programme is in place with regular monitoring and treatment records.',
    'Review pest control service reports for the current period. Confirm monitoring points are inspected on schedule. Check any action taken on positive findings.',
    90, 9
  UNION ALL SELECT 'GMP-010', 'Calibration of Measuring Equipment',
    'All critical measuring equipment is calibrated at defined intervals with records maintained.',
    'Check calibration register for overdue items. Verify calibration certificates are available and traceable to national standards.',
    180, 10
) AS checks ON c.code = 'GMP';

-- ============================================================
-- GLP (Good Laboratory Practices) checks
-- ============================================================
INSERT IGNORE INTO compliance_checks
  (category_id, code, title, description, guidance, default_review_interval_days, default_evidence_required, is_predefined, is_active, sort_order)
SELECT
  c.id,
  checks.code,
  checks.title,
  checks.description,
  checks.guidance,
  checks.interval_days,
  1,
  1,
  1,
  checks.sort_order
FROM compliance_check_categories c
JOIN (
  SELECT
    'GLP-001'                              AS code,
    'Master Schedule Maintenance'          AS title,
    'The study Master Schedule is kept current and reflects the status of all ongoing and completed studies.' AS description,
    'Verify the Master Schedule was updated within the required period. Confirm all study entries include sponsor, title, test system, and status.' AS guidance,
    90  AS interval_days,
    1   AS sort_order
  UNION ALL SELECT 'GLP-002', 'Study Director Assignment',
    'Each study has an assigned Study Director who is qualified and documented in the study plan.',
    'Confirm each active study has a named, qualified Study Director. Check the assignment is formally documented and current.',
    180, 2
  UNION ALL SELECT 'GLP-003', 'Approved Study Plans & Protocols',
    'All studies are conducted according to a written, approved study plan signed by the Study Director.',
    'Sample active study plans. Verify each is signed by the Study Director and dated prior to study commencement. Confirm amendments are documented.',
    180, 3
  UNION ALL SELECT 'GLP-004', 'SOPs Covering All GLP Activities',
    'Written SOPs exist for all GLP-relevant activities and are available to personnel performing those activities.',
    'Review the SOP index against GLP activities performed. Verify SOPs are current versions and accessible at point of use.',
    365, 4
  UNION ALL SELECT 'GLP-005', 'Test & Reference Item Characterisation & Storage',
    'Test and reference items are characterised, labelled, and stored under appropriate conditions.',
    'Check characterisation data (identity, purity, stability) is available for current items. Inspect storage conditions and temperature logs.',
    180, 5
  UNION ALL SELECT 'GLP-006', 'Equipment Calibration & Maintenance Records',
    'Laboratory equipment is calibrated, maintained, and records are kept in equipment logs.',
    'Review equipment logs for calibration currency. Confirm maintenance records identify the equipment, date, person responsible, and results.',
    180, 6
  UNION ALL SELECT 'GLP-007', 'Computer System Validation',
    'Computer systems used in GLP studies are validated and change-controlled.',
    'Verify validation documentation exists for each regulated system. Check that changes are subject to impact assessment and re-validation where required.',
    365, 7
  UNION ALL SELECT 'GLP-008', 'Archive of Raw Data & Specimens',
    'Raw data and specimens are archived securely with defined retention periods and access controls.',
    'Inspect the archive facility for appropriate conditions. Confirm index is current and access log is maintained. Verify retention periods match regulatory requirements.',
    365, 8
  UNION ALL SELECT 'GLP-009', 'QA Unit Inspections',
    'The Quality Assurance Unit conducts scheduled inspections of ongoing studies and the facility.',
    'Review the QA inspection schedule and completion status. Check that inspection reports have been issued and Management/Study Director acknowledgements obtained.',
    180, 9
  UNION ALL SELECT 'GLP-010', 'Final Report Sign-off',
    'Completed studies have a final report signed by the Study Director and reviewed by QA.',
    'Sample completed study final reports. Verify Study Director signature, QA statement, and that the report reflects the raw data.',
    365, 10
) AS checks ON c.code = 'GLP';
