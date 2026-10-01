-- Surface spare Microsoft 365 seats by company and product in the reporting
-- catalog so the result can also be used as a configurable dashboard panel.
-- Keep the allocation calculation aligned with the Licenses page: direct staff
-- assignments and memberships of license-bearing office groups are de-duplicated.
INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system)
VALUES (
    'dashboard-global-available-licenses',
    'Dashboard - Available licenses',
    'Available Microsoft 365 license seats by company and product. Products hidden in the MyPortal license SKU mappings are excluded.',
    'SELECT license_totals.company_id, license_totals.company, license_totals.product, license_totals.total_licenses, license_totals.allocated_licenses, GREATEST(license_totals.total_licenses - license_totals.allocated_licenses, 0) AS available_licenses FROM (SELECT c.id AS company_id, c.name AS company, COALESCE(NULLIF(TRIM(lsn.friendly_name), ''''), l.name) AS product, l.count AS total_licenses, (SELECT COUNT(DISTINCT s.id) FROM staff s WHERE s.id IN (SELECT sl.staff_id FROM staff_licenses sl WHERE sl.license_id = l.id UNION SELECT ogm.staff_id FROM group_licenses gl INNER JOIN office_group_members ogm ON ogm.group_id = gl.group_id WHERE gl.license_id = l.id)) AS allocated_licenses FROM licenses l INNER JOIN companies c ON c.id = l.company_id LEFT JOIN license_sku_friendly_names lsn ON lsn.sku = l.platform WHERE COALESCE(lsn.hidden, 0) = 0) license_totals ORDER BY license_totals.company ASC, license_totals.product ASC',
    1
);
