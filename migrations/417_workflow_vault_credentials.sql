-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

ALTER TABLE credentials ADD COLUMN workflow_execution_id BIGINT NULL;
ALTER TABLE credentials ADD COLUMN workflow_step_identity VARCHAR(255) NULL;
CREATE UNIQUE INDEX uq_credentials_workflow_step
    ON credentials (workflow_execution_id, workflow_step_identity);
