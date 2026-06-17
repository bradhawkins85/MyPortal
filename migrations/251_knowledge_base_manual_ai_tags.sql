-- Store admin-added knowledge base AI tags separately from AI-generated tags.
-- Manual tags are preserved when generated tags are refreshed.
ALTER TABLE knowledge_base_articles
    ADD COLUMN IF NOT EXISTS manual_ai_tags JSON NULL AFTER excluded_ai_tags;

UPDATE knowledge_base_articles
SET manual_ai_tags = JSON_ARRAY()
WHERE manual_ai_tags IS NULL;
