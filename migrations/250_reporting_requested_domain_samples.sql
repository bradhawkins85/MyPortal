-- Add additional system reporting queries for requested functional domains.
-- Idempotent via INSERT IGNORE on the unique slug.
INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system)
VALUES
    (
        'backup-jobs-health-last-30-days',
        'Backups: Job Health (Last 30 Days)',
        'Per-backup-job status summary over the last 30 days, including pass/fail/warn counts and latest report time.',
        'SELECT c.name AS company, bj.name AS backup_job, COUNT(*) AS total_events, SUM(CASE WHEN bje.status = ''pass'' THEN 1 ELSE 0 END) AS pass_events, SUM(CASE WHEN bje.status = ''fail'' THEN 1 ELSE 0 END) AS fail_events, SUM(CASE WHEN bje.status = ''warn'' THEN 1 ELSE 0 END) AS warn_events, MAX(bje.reported_at) AS last_reported_at FROM backup_job_events bje JOIN backup_jobs bj ON bj.id = bje.backup_job_id JOIN companies c ON c.id = bj.company_id WHERE bje.event_date >= (CURRENT_DATE - INTERVAL 30 DAY) GROUP BY c.id, c.name, bj.id, bj.name ORDER BY fail_events DESC, warn_events DESC, company ASC, backup_job ASC',
        1
    ),
    (
        'kb-published-by-scope',
        'Knowledge Base: Published Articles by Scope',
        'Count of published knowledge base articles by permission scope, with section counts.',
        'SELECT kba.permission_scope, COUNT(DISTINCT kba.id) AS published_articles, COUNT(kbs.id) AS total_sections FROM knowledge_base_articles kba LEFT JOIN knowledge_base_sections kbs ON kbs.article_id = kba.id WHERE kba.is_published = 1 GROUP BY kba.permission_scope ORDER BY published_articles DESC, kba.permission_scope ASC',
        1
    ),
    (
        'chat-room-activity-last-14-days',
        'Chats: Room Activity (Last 14 Days)',
        'Open and closed chat room activity over the last 14 days with message counts and last message timestamp.',
        'SELECT c.name AS company, cr.status, COUNT(DISTINCT cr.id) AS room_count, COUNT(cm.id) AS message_count, MAX(cm.sent_at) AS last_message_at FROM chat_rooms cr JOIN companies c ON c.id = cr.company_id LEFT JOIN chat_messages cm ON cm.room_id = cr.id AND cm.sent_at >= (CURRENT_DATE - INTERVAL 14 DAY) GROUP BY c.id, c.name, cr.status ORDER BY message_count DESC, company ASC, cr.status ASC',
        1
    ),
    (
        'office365-best-practice-status-by-company',
        'Office 365: Best Practice Status by Company',
        'Current Microsoft 365 best-practice status counts per company.',
        'SELECT c.name AS company, SUM(CASE WHEN r.status = ''pass'' THEN 1 ELSE 0 END) AS pass_checks, SUM(CASE WHEN r.status = ''fail'' THEN 1 ELSE 0 END) AS fail_checks, SUM(CASE WHEN r.status = ''unknown'' THEN 1 ELSE 0 END) AS unknown_checks, SUM(CASE WHEN r.status = ''not_applicable'' THEN 1 ELSE 0 END) AS not_applicable_checks, MAX(r.run_at) AS last_run_at FROM m365_best_practice_results r JOIN companies c ON c.id = r.company_id GROUP BY c.id, c.name ORDER BY fail_checks DESC, unknown_checks DESC, company ASC',
        1
    ),
    (
        'compliance-assignment-status-by-company',
        'Compliance: Assignment Status by Company',
        'Compliance assignment counts per company split by status (excluding archived assignments).',
        'SELECT c.name AS company, SUM(CASE WHEN cca.status = ''compliant'' THEN 1 ELSE 0 END) AS compliant_count, SUM(CASE WHEN cca.status = ''non_compliant'' THEN 1 ELSE 0 END) AS non_compliant_count, SUM(CASE WHEN cca.status = ''in_progress'' THEN 1 ELSE 0 END) AS in_progress_count, SUM(CASE WHEN cca.status = ''not_started'' THEN 1 ELSE 0 END) AS not_started_count, COUNT(*) AS total_assignments FROM company_compliance_check_assignments cca JOIN companies c ON c.id = cca.company_id WHERE cca.archived = 0 GROUP BY c.id, c.name ORDER BY non_compliant_count DESC, in_progress_count DESC, company ASC',
        1
    ),
    (
        'continuity-plan-status-by-type',
        'Continuity: Plan Status by Type',
        'Business continuity, disaster recovery, and incident response plan counts by status and type.',
        'SELECT bcp.plan_type, bcp.status, COUNT(*) AS plan_count, MAX(bcp.updated_at) AS latest_update_at FROM business_continuity_plans bcp GROUP BY bcp.plan_type, bcp.status ORDER BY bcp.plan_type ASC, plan_count DESC, bcp.status ASC',
        1
    ),
    (
        'shop-product-stock-and-value',
        'Shops: Product Stock and Inventory Value',
        'Shop product inventory position with stock levels and estimated inventory value.',
        'SELECT sp.id AS product_id, sp.name AS product, sp.stock, sp.price, ROUND(sp.stock * sp.price, 2) AS inventory_value FROM shop_products sp ORDER BY inventory_value DESC, sp.name ASC',
        1
    ),
    (
        'shop-orders-by-company-last-30-days',
        'Orders: Company Order Volume (Last 30 Days)',
        'Shop order counts and quantities by company for the last 30 days.',
        'SELECT c.name AS company, COUNT(so.id) AS order_count, SUM(so.quantity) AS total_quantity, MIN(so.order_date) AS first_order_at, MAX(so.order_date) AS last_order_at FROM shop_orders so JOIN companies c ON c.id = so.company_id WHERE so.order_date >= (CURRENT_DATE - INTERVAL 30 DAY) GROUP BY c.id, c.name ORDER BY order_count DESC, total_quantity DESC, company ASC',
        1
    ),
    (
        'shop-quotes-status-and-expiry',
        'Quotes: Status and Expiry Overview',
        'Shop quote counts by status and expiry window to help follow up pending quotes.',
        'SELECT sq.status, COUNT(*) AS quote_count, SUM(CASE WHEN sq.expires_at < NOW() THEN 1 ELSE 0 END) AS expired_quotes, SUM(CASE WHEN sq.expires_at >= NOW() THEN 1 ELSE 0 END) AS unexpired_quotes FROM shop_quotes sq GROUP BY sq.status ORDER BY quote_count DESC, sq.status ASC',
        1
    ),
    (
        'subscriptions-status-by-company',
        'Subscriptions: Status by Company',
        'Subscription counts by company and status, with upcoming expiries in the next 30 days.',
        'SELECT c.name AS company, s.status, COUNT(*) AS subscription_count, SUM(CASE WHEN s.end_date >= CURRENT_DATE AND s.end_date < (CURRENT_DATE + INTERVAL 30 DAY) THEN 1 ELSE 0 END) AS expiring_next_30_days FROM subscriptions s JOIN companies c ON c.id = s.customer_id GROUP BY c.id, c.name, s.status ORDER BY company ASC, subscription_count DESC, s.status ASC',
        1
    ),
    (
        'invoices-status-and-overdue-by-company',
        'Invoices: Status and Overdue by Company',
        'Invoice totals and overdue counts by company.',
        'SELECT c.name AS company, COALESCE(i.status, ''(unspecified)'') AS invoice_status, COUNT(*) AS invoice_count, ROUND(SUM(i.amount), 2) AS total_amount, SUM(CASE WHEN i.due_date IS NOT NULL AND i.due_date < CURRENT_DATE THEN 1 ELSE 0 END) AS overdue_count FROM invoices i JOIN companies c ON c.id = i.company_id GROUP BY c.id, c.name, i.status ORDER BY overdue_count DESC, total_amount DESC, company ASC',
        1
    );
