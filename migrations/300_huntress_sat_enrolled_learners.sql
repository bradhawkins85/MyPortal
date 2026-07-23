-- Store Huntress Managed SAT active learner counts for reporting and billing variables.
ALTER TABLE huntress_sat_stats
  ADD COLUMN enrolled_learners INT NOT NULL DEFAULT 0 AFTER company_id;
