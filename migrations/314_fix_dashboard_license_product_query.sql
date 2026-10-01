-- The licenses table stores the product label in `name`; it has no
-- `product_name` column. Repair the bundled report for existing installations.
UPDATE reporting_queries
SET sql_query = 'SELECT COALESCE(name, ''Other'') AS X, COUNT(*) AS Y FROM licenses WHERE company_id = {{current.company}} GROUP BY name ORDER BY Y DESC'
WHERE slug = 'dashboard-licenses-by-product'
  AND is_system = 1;
