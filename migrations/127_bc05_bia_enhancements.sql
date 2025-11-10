-- BC05: Business Impact Analysis (BIA) Enhancements
-- Adds importance rating and additional impact fields for comprehensive BIA

-- Add importance field to critical activities (1-5, where 1 is most important)
ALTER TABLE bcp_critical_activity 
ADD COLUMN IF NOT EXISTS importance INT COMMENT 'Importance rating 1-5 (1=most important)';

-- Add index for importance field to support sorting
CREATE INDEX IF NOT EXISTS idx_bcp_critical_activity_importance 
ON bcp_critical_activity(importance);

-- Add constraint to ensure importance is between 1 and 5
ALTER TABLE bcp_critical_activity 
ADD CONSTRAINT IF NOT EXISTS ck_importance_range 
CHECK (importance >= 1 AND importance <= 5 OR importance IS NULL);

-- Add additional loss impact fields to bcp_impact table
ALTER TABLE bcp_impact 
ADD COLUMN IF NOT EXISTS losses_increased_costs TEXT COMMENT 'Increased costs impact description';

ALTER TABLE bcp_impact 
ADD COLUMN IF NOT EXISTS losses_product_service TEXT COMMENT 'Product/service delivery impact description';

ALTER TABLE bcp_impact 
ADD COLUMN IF NOT EXISTS losses_comments TEXT COMMENT 'Additional comments on losses and impacts';
