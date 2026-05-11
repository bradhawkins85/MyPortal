-- Solidtime integration mapping tables.
--
-- These tables associate MyPortal entities (companies, tickets, ticket replies,
-- users) with their counterparts in Solidtime so that changes can be
-- bidirectionally propagated without losing track of the link or breaking
-- update loops.
--
-- The migration is fully idempotent: it only creates tables when they are
-- missing, so it is safe to run repeatedly during development upgrades.

CREATE TABLE IF NOT EXISTS solidtime_client_links (
    company_id INT NOT NULL PRIMARY KEY,
    solidtime_org_id VARCHAR(64) NOT NULL,
    solidtime_client_id VARCHAR(64) NOT NULL,
    last_synced_at DATETIME(6) NULL,
    sync_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    last_error TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    KEY idx_solidtime_client_links_org (solidtime_org_id),
    CONSTRAINT fk_solidtime_client_links_company FOREIGN KEY (company_id)
        REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS solidtime_project_links (
    ticket_id INT NOT NULL PRIMARY KEY,
    solidtime_org_id VARCHAR(64) NOT NULL,
    solidtime_project_id VARCHAR(64) NOT NULL,
    last_synced_at DATETIME(6) NULL,
    last_payload_hash CHAR(64) NULL,
    sync_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    last_error TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    KEY idx_solidtime_project_links_org (solidtime_org_id),
    KEY idx_solidtime_project_links_remote (solidtime_project_id),
    CONSTRAINT fk_solidtime_project_links_ticket FOREIGN KEY (ticket_id)
        REFERENCES tickets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS solidtime_time_entry_links (
    ticket_reply_id INT NOT NULL PRIMARY KEY,
    solidtime_org_id VARCHAR(64) NOT NULL,
    solidtime_time_entry_id VARCHAR(64) NOT NULL,
    direction ENUM('out', 'in') NOT NULL DEFAULT 'out',
    last_synced_at DATETIME(6) NULL,
    last_payload_hash CHAR(64) NULL,
    sync_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    last_error TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_solidtime_time_entry_links_remote (solidtime_org_id, solidtime_time_entry_id),
    CONSTRAINT fk_solidtime_time_entry_links_reply FOREIGN KEY (ticket_reply_id)
        REFERENCES ticket_replies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS solidtime_user_links (
    user_id INT NOT NULL PRIMARY KEY,
    solidtime_org_id VARCHAR(64) NOT NULL,
    solidtime_member_id VARCHAR(64) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    KEY idx_solidtime_user_links_org (solidtime_org_id),
    CONSTRAINT fk_solidtime_user_links_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE
);
