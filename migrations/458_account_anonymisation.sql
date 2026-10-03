-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Account anonymisation (GDPR "right to be forgotten", issue #4554).
--
-- Records a user's request to have their personally identifiable information
-- anonymised across the portal. A request is created when the user submits the
-- profile form and a support ticket is raised; a super admin then executes the
-- run, which NULLs/overwrites PII in the affected tables, deletes their
-- sessions, staff identity, ticket/requester links, call/SMS/chat records and
-- RAG documents, and deactivates the account.
--
-- The run is idempotent: it is safe to retry after a partial failure, and an
-- already-anonymised account simply remains in its anonymised state.
CREATE TABLE account_anonymisation_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    original_email_hash CHAR(64) NULL,
    reason TEXT NULL,
    requested_by_user_id INT NULL,
    support_ticket_id INT NULL,
    requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    scheduled_for DATETIME NULL,
    executed_by_user_id INT NULL,
    executed_at DATETIME NULL,
    error_message TEXT NULL,
    ip_address VARCHAR(45) NULL,
    user_agent TEXT NULL,
    CONSTRAINT uq_account_anonymisation_requests_user
        UNIQUE (user_id),
    CONSTRAINT fk_account_anonymisation_requests_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_account_anonymisation_requests_requested_by
        FOREIGN KEY (requested_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_account_anonymisation_requests_executed_by
        FOREIGN KEY (executed_by_user_id) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_account_anonymisation_requests_status
    ON account_anonymisation_requests (status, requested_at);
