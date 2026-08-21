-- One mutable state row per tenant-owned endpoint serializes threshold claims.
CREATE TABLE voice_monitor_incidents (
    company_id INT NOT NULL,
    endpoint_id BIGINT UNSIGNED NOT NULL,
    consecutive_failures INT UNSIGNED NOT NULL DEFAULT 0,
    ticket_id INT NULL,
    ticket_claim_attempt_id BIGINT UNSIGNED NULL,
    opened_at DATETIME(6) NULL,
    recovered_at DATETIME(6) NULL,
    PRIMARY KEY (company_id, endpoint_id),
    UNIQUE INDEX uq_voice_monitor_incident_claim (ticket_claim_attempt_id),
    CONSTRAINT fk_vm_incident_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE RESTRICT,
    CONSTRAINT fk_vm_incident_endpoint FOREIGN KEY (endpoint_id) REFERENCES voice_monitor_endpoints(id) ON DELETE CASCADE,
    CONSTRAINT fk_vm_incident_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL,
    CONSTRAINT fk_vm_incident_claim FOREIGN KEY (ticket_claim_attempt_id) REFERENCES voice_monitor_attempts(id) ON DELETE SET NULL
);
