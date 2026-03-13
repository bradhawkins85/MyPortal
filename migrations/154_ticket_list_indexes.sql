-- Improve ticket list/count query performance for common filters + updated_at sorting

CREATE INDEX IF NOT EXISTS idx_tickets_updated_at
    ON tickets (updated_at);

CREATE INDEX IF NOT EXISTS idx_tickets_status_updated_at
    ON tickets (status, updated_at);

CREATE INDEX IF NOT EXISTS idx_tickets_company_updated_at
    ON tickets (company_id, updated_at);

CREATE INDEX IF NOT EXISTS idx_tickets_assigned_user_updated_at
    ON tickets (assigned_user_id, updated_at);

CREATE INDEX IF NOT EXISTS idx_tickets_requester_updated_at
    ON tickets (requester_id, updated_at);

-- Optimise watcher join path used by user ticket list/count queries
CREATE INDEX IF NOT EXISTS idx_ticket_watchers_user_ticket_id
    ON ticket_watchers (user_id, ticket_id);
