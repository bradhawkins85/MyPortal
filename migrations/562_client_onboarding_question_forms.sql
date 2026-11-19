-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Custom onboarding questions can be a "custom form": a set of sub-questions
-- the client fills in once per entry (e.g. stakeholders or vendors). The
-- entry name and sub-question definitions are stored as JSON on the question.
ALTER TABLE client_onboarding_questions ADD COLUMN IF NOT EXISTS form_json LONGTEXT NULL;
