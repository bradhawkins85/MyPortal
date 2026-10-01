-- phase: data
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Add a reusable webhook audit search to the Reporting catalogue. Administrators
-- can clone the query and replace the example name and response-code filters.
INSERT IGNORE INTO reporting_queries (slug, name, description, sql_query, is_system)
VALUES (
    'webhook-events-by-name-and-response-code',
    'Webhook Events - By Name and Response Code',
    'Webhook attempts filtered by event name and HTTP response code, including the URL and sanitized request and response data. Clone and replace "webhook name" and 200 in the filters.',
    'SELECT e.id AS event_id, a.id AS attempt_id, e.name, e.direction, COALESCE(e.source_url, e.target_url) AS url, a.attempt_number, a.response_status AS response_code, a.request_body AS request_data, a.response_body AS response_data, a.status AS attempt_status, a.error_message, a.attempted_at, e.created_at AS event_created_at FROM webhook_events e INNER JOIN webhook_event_attempts a ON a.event_id = e.id WHERE e.name LIKE ''%webhook name%'' AND a.response_status = 200 ORDER BY a.attempted_at DESC, a.id DESC',
    1
);
