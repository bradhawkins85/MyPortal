-- Add excluded_ai_tags field to track tags manually removed by admins
-- These tags should not be automatically re-added when AI tags are refreshed
ALTER TABLE knowledge_base_articles
    ADD COLUMN IF NOT EXISTS excluded_ai_tags JSON NULL AFTER ai_tags;

-- Initialize to empty array for existing articles
UPDATE knowledge_base_articles
SET excluded_ai_tags = JSON_ARRAY()
WHERE excluded_ai_tags IS NULL;
