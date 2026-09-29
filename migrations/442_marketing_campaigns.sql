-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 442: Marketing email campaigns. A campaign targets contacts chosen
-- by audience filters, is sent one recipient at a time during business hours,
-- and records SMTP2Go engagement plus any ticket created from a reply.
CREATE TABLE IF NOT EXISTS marketing_campaigns (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(160) NOT NULL,
    category VARCHAR(16) NOT NULL DEFAULT 'updates',
    subject VARCHAR(255) NOT NULL,
    message_template_id INT NULL,
    body_html MEDIUMTEXT NULL,
    sender_email VARCHAR(255) NULL,
    sender_name VARCHAR(160) NULL,
    reply_to VARCHAR(255) NULL,
    audience JSON NOT NULL,
    business_hours_source VARCHAR(16) NOT NULL DEFAULT 'company',
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    scheduled_for DATETIME NULL,
    created_by INT NULL,
    queued_at DATETIME NULL,
    completed_at DATETIME NULL,
    cancelled_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_marketing_campaigns_template FOREIGN KEY (message_template_id) REFERENCES message_templates(id) ON DELETE SET NULL,
    CONSTRAINT fk_marketing_campaigns_creator FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_marketing_campaigns_status ON marketing_campaigns (status);

CREATE TABLE IF NOT EXISTS marketing_campaign_recipients (
    id INT AUTO_INCREMENT PRIMARY KEY,
    campaign_id INT NOT NULL,
    staff_id INT NULL,
    company_id INT NULL,
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255) NULL,
    token CHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    skip_reason VARCHAR(64) NULL,
    subject_rendered VARCHAR(255) NULL,
    message_id VARCHAR(255) NULL,
    smtp2go_message_id VARCHAR(255) NULL,
    provider VARCHAR(32) NULL,
    send_after DATETIME NULL,
    sent_at DATETIME NULL,
    delivered_at DATETIME NULL,
    first_opened_at DATETIME NULL,
    open_count INT NOT NULL DEFAULT 0,
    first_clicked_at DATETIME NULL,
    click_count INT NOT NULL DEFAULT 0,
    bounced_at DATETIME NULL,
    error_message TEXT NULL,
    reply_ticket_id INT NULL,
    replied_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_marketing_recipients_campaign FOREIGN KEY (campaign_id) REFERENCES marketing_campaigns(id) ON DELETE CASCADE,
    CONSTRAINT fk_marketing_recipients_staff FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE SET NULL,
    CONSTRAINT fk_marketing_recipients_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL,
    CONSTRAINT fk_marketing_recipients_ticket FOREIGN KEY (reply_ticket_id) REFERENCES tickets(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX IF NOT EXISTS uq_marketing_recipients_campaign_email ON marketing_campaign_recipients (campaign_id, email);
CREATE UNIQUE INDEX IF NOT EXISTS uq_marketing_recipients_token ON marketing_campaign_recipients (token);
CREATE INDEX IF NOT EXISTS idx_marketing_recipients_due ON marketing_campaign_recipients (status, send_after);
CREATE INDEX IF NOT EXISTS idx_marketing_recipients_smtp2go ON marketing_campaign_recipients (smtp2go_message_id);
CREATE INDEX IF NOT EXISTS idx_marketing_recipients_email ON marketing_campaign_recipients (email, sent_at);

CREATE TABLE IF NOT EXISTS marketing_email_opt_outs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    category VARCHAR(16) NOT NULL DEFAULT 'sales',
    campaign_id INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_marketing_opt_outs_campaign FOREIGN KEY (campaign_id) REFERENCES marketing_campaigns(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX IF NOT EXISTS uq_marketing_opt_outs_email_category ON marketing_email_opt_outs (email, category);
