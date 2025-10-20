ALTER TABLE knowledge_base_articles
    ADD COLUMN ai_tags JSON NULL AFTER summary;

UPDATE knowledge_base_articles
SET ai_tags = JSON_ARRAY()
WHERE ai_tags IS NULL;
