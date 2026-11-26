-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 578: customer satisfaction (CSAT) surveys for resolved tickets.
--
-- csat_settings holds the single row of survey settings (off until an admin
-- switches it on). ticket_csat_surveys holds one survey per ticket: only the
-- SHA-256 hash of the emailed link's token is stored. A rating at or below the
-- low-score threshold opens a follow-up for the team. Companies listed in
-- csat_company_exclusions never receive surveys.

CREATE TABLE IF NOT EXISTS csat_settings (
    id INT NOT NULL PRIMARY KEY,
    enabled TINYINT(1) NOT NULL DEFAULT 0,
    delay_minutes INT NOT NULL DEFAULT 60,
    low_score_threshold TINYINT NOT NULL DEFAULT 2,
    expiry_days INT NOT NULL DEFAULT 14,
    enabled_at DATETIME NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO csat_settings (id, enabled, delay_minutes, low_score_threshold, expiry_days)
VALUES (1, 0, 60, 2, 14);

CREATE TABLE IF NOT EXISTS csat_company_exclusions (
    company_id INT NOT NULL PRIMARY KEY,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_csat_company_exclusions_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ticket_csat_surveys (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ticket_id INT NOT NULL,
    company_id INT NULL,
    technician_user_id INT NULL,
    recipient_email VARCHAR(255) NULL,
    token_hash CHAR(64) NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    rating TINYINT NULL,
    comment TEXT NULL,
    error_message VARCHAR(500) NULL,
    follow_up_status VARCHAR(16) NULL,
    followed_up_by_user_id INT NULL,
    followed_up_at DATETIME NULL,
    sent_at DATETIME NULL,
    expires_at DATETIME NULL,
    responded_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uq_ticket_csat_surveys_ticket UNIQUE (ticket_id),
    CONSTRAINT uq_ticket_csat_surveys_token UNIQUE (token_hash),
    CONSTRAINT fk_ticket_csat_surveys_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
    CONSTRAINT fk_ticket_csat_surveys_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_ticket_csat_surveys_responded ON ticket_csat_surveys (status, responded_at);
CREATE INDEX idx_ticket_csat_surveys_follow_up ON ticket_csat_surveys (follow_up_status);

INSERT IGNORE INTO message_templates (slug, name, description, content_type, content) VALUES
  (
    'ticket-csat-survey-subject',
    'Ticket satisfaction survey subject',
    'Subject line of the email asking a requester to rate a resolved ticket. Variables: {{ recipient.first_name }}, {{ ticket.number }}, {{ ticket.subject }}, {{ app.name }}.',
    'text/plain',
    'How did we do? Ticket {{ ticket.number }}: {{ ticket.subject }}'
  ),
  (
    'ticket-csat-survey',
    'Ticket satisfaction survey',
    'Emailed to the requester once a ticket is resolved. Each rating link opens a page where they confirm the rating and can leave a comment. Variables: {{ recipient.first_name }}, {{ ticket.number }}, {{ ticket.subject }}, {{ technician.name }}, {{ survey.link }}, {{ survey.link_1 }} to {{ survey.link_5 }}, {{ survey.expires }}, {{ app.name }}.',
    'text/html',
    '<p>Hi {{ recipient.first_name }},</p><p>We recently resolved ticket {{ ticket.number }}: <strong>{{ ticket.subject }}</strong>. How satisfied were you with the support you received?</p><p><a href="{{ survey.link_5 }}">Very satisfied</a> &middot; <a href="{{ survey.link_4 }}">Satisfied</a> &middot; <a href="{{ survey.link_3 }}">Neutral</a> &middot; <a href="{{ survey.link_2 }}">Dissatisfied</a> &middot; <a href="{{ survey.link_1 }}">Very dissatisfied</a></p><p>It takes a few seconds, and you can add a comment on the next page. This link works until {{ survey.expires }}.</p><p>Thank you,<br>{{ app.name }}</p>'
  );
