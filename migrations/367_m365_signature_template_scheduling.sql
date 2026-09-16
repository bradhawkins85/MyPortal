ALTER TABLE m365_signature_templates
    ADD COLUMN priority INTEGER NOT NULL DEFAULT 0 AFTER status,
    ADD COLUMN is_default BOOLEAN NOT NULL DEFAULT FALSE AFTER priority,
    ADD COLUMN schedule_start_on DATE NULL AFTER is_default,
    ADD COLUMN schedule_end_on DATE NULL AFTER schedule_start_on;
