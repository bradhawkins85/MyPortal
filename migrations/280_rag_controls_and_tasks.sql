CREATE TABLE IF NOT EXISTS rag_matching_state (
    `key` VARCHAR(64) NOT NULL PRIMARY KEY,
    value VARCHAR(255) NOT NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO rag_matching_state (`key`, value)
SELECT 'paused', '0'
WHERE NOT EXISTS (SELECT 1 FROM rag_matching_state WHERE `key` = 'paused');

INSERT INTO scheduled_tasks (name, command, cron, active, description)
SELECT 'RAG Start Indexing', 'rag_index_start', '0 2 * * *', 0, 'Start a RAG indexing job.'
WHERE NOT EXISTS (SELECT 1 FROM scheduled_tasks WHERE command = 'rag_index_start');

INSERT INTO scheduled_tasks (name, command, cron, active, description)
SELECT 'RAG Stop Indexing', 'rag_index_stop', '0 6 * * *', 0, 'Force stop any active RAG indexing job.'
WHERE NOT EXISTS (SELECT 1 FROM scheduled_tasks WHERE command = 'rag_index_stop');

INSERT INTO scheduled_tasks (name, command, cron, active, description)
SELECT 'RAG Pause Matching', 'rag_matching_pause', '0 7 * * *', 0, 'Pause RAG document matching workers.'
WHERE NOT EXISTS (SELECT 1 FROM scheduled_tasks WHERE command = 'rag_matching_pause');

INSERT INTO scheduled_tasks (name, command, cron, active, description)
SELECT 'RAG Resume Matching', 'rag_matching_resume', '0 8 * * *', 0, 'Resume RAG document matching workers.'
WHERE NOT EXISTS (SELECT 1 FROM scheduled_tasks WHERE command = 'rag_matching_resume');

INSERT INTO scheduled_tasks (name, command, cron, active, description)
SELECT 'RAG Cleanup Stale Matches', 'rag_cleanup_stale_matches', '30 3 * * *', 0, 'Delete stale RAG matches/decisions and reset abandoned matching jobs.'
WHERE NOT EXISTS (SELECT 1 FROM scheduled_tasks WHERE command = 'rag_cleanup_stale_matches');
