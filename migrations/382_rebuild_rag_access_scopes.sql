-- Legacy shared-index rows did not carry sufficient current-user access data.
-- Purge them atomically; the normal maintenance job rebuilds them with v1 scopes.
DELETE FROM rag_documents;
