ALTER TABLE approval_configurations
    ADD COLUMN IF NOT EXISTS guid CHAR(36) NULL AFTER id;

UPDATE approval_configurations
SET guid = UUID()
WHERE guid IS NULL OR guid = '';

ALTER TABLE approval_configurations
    MODIFY COLUMN guid CHAR(36) NOT NULL;

CREATE UNIQUE INDEX uq_approval_configurations_guid
    ON approval_configurations (guid);
