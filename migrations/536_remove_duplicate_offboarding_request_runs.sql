-- phase: data
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 536: remove the extra offboarding workflow runs that portal
-- offboarding requests used to create before approval. Approval queues its own
-- run, so each of these was left in the workflow history as a duplicate
-- "Requested" run that never started. Only offboarding runs that were never
-- picked up (no current step, never started or finished, no step logs and no
-- external checkpoints) are removed; claimed runs always have a current step.
DELETE FROM staff_onboarding_workflow_executions
WHERE direction = 'offboarding'
  AND state = 'requested'
  AND current_step IS NULL
  AND started_at IS NULL
  AND completed_at IS NULL
  AND NOT EXISTS (
      SELECT 1
      FROM staff_onboarding_workflow_step_logs AS logs
      WHERE logs.execution_id = staff_onboarding_workflow_executions.id
  )
  AND NOT EXISTS (
      SELECT 1
      FROM staff_onboarding_external_checkpoints AS checkpoints
      WHERE checkpoints.execution_id = staff_onboarding_workflow_executions.id
  );
