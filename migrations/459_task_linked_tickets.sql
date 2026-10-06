-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 459: Support "linked tickets" created from ticket tasks.
--
-- A task on a main ticket can spawn a standalone child ticket that references
-- back to the main ticket and supports its own assignment and notes/replies.
--   * ticket_tasks.linked_ticket_id records which ticket was created for a
--     specific task, so the UI button can simply reopen it afterwards.
--   * tickets.parent_ticket_id lets a linked (child) ticket reference the
--     main ticket it was spawned from.
--
-- Only `ADD COLUMN` is used (no separate index/FK DDL) so the migration applies
-- cleanly on both MySQL and the SQLite fallback used for tests.
ALTER TABLE ticket_tasks
    ADD COLUMN IF NOT EXISTS linked_ticket_id INT NULL;

ALTER TABLE tickets
    ADD COLUMN IF NOT EXISTS parent_ticket_id INT NULL;