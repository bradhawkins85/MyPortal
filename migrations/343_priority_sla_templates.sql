CREATE TABLE IF NOT EXISTS sla_templates (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(150) NOT NULL,
    description TEXT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sla_template_targets (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    template_id INTEGER NOT NULL,
    priority VARCHAR(32) NOT NULL,
    response_minutes INTEGER NOT NULL,
    resolution_minutes INTEGER NOT NULL,
    UNIQUE KEY uq_sla_template_priority (template_id, priority),
    CONSTRAINT fk_sla_target_template FOREIGN KEY (template_id) REFERENCES sla_templates(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_sla_templates (
    company_id INTEGER NOT NULL PRIMARY KEY,
    template_id INTEGER NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_company_sla_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_company_sla_template FOREIGN KEY (template_id) REFERENCES sla_templates(id) ON DELETE CASCADE
);

-- Preserve existing blanket SLAs as templates with targets for the priorities
-- that existed before priority-aware SLAs were introduced.
INSERT INTO sla_templates (id, name, enabled, created_at, updated_at)
SELECT id, name, enabled, created_at, updated_at FROM service_level_agreements;

INSERT INTO sla_template_targets (template_id, priority, response_minutes, resolution_minutes)
SELECT id, priorities.priority, response_minutes, resolution_minutes
FROM service_level_agreements
CROSS JOIN (
    SELECT 'urgent' AS priority UNION ALL SELECT 'high' UNION ALL
    SELECT 'normal' UNION ALL SELECT 'low'
) priorities;

-- Tickets can contain administrator-defined priority values. Carry every such
-- value into migrated templates as well instead of assuming the default four.
INSERT IGNORE INTO sla_template_targets (template_id, priority, response_minutes, resolution_minutes)
SELECT s.id, LOWER(TRIM(t.priority)), s.response_minutes, s.resolution_minutes
FROM service_level_agreements s
CROSS JOIN tickets t
WHERE TRIM(COALESCE(t.priority, '')) <> ''
GROUP BY s.id, LOWER(TRIM(t.priority)), s.response_minutes, s.resolution_minutes;

INSERT INTO company_sla_templates (company_id, template_id)
SELECT company_id, id FROM service_level_agreements;
