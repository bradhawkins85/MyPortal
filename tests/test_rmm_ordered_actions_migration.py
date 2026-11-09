from pathlib import Path


MIGRATION = Path(__file__).parent.parent / "migrations" / "547_rmm_ordered_actions.sql"


def test_ordered_actions_migration_is_an_idempotent_expand():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.startswith("-- phase: expand")
    assert "ALTER TABLE rmm_checks ADD COLUMN IF NOT EXISTS actions_json LONGTEXT NULL;" in sql
    assert "ALTER TABLE rmm_event_log_rules ADD COLUMN IF NOT EXISTS actions_json LONGTEXT NULL;" in sql
    assert "CREATE TABLE IF NOT EXISTS rmm_check_action_schedules" in sql
    assert "CREATE TABLE IF NOT EXISTS rmm_event_log_rule_action_schedules" in sql
    # One waiting run per action and device, so several scheduled scripts can wait at once.
    assert "PRIMARY KEY (check_id, agent_id, action_key)" in sql
    assert "PRIMARY KEY (rule_id, agent_id, action_key)" in sql
    assert "REFERENCES rmm_checks(id) ON DELETE CASCADE" in sql
    assert "REFERENCES rmm_event_log_rules(id) ON DELETE CASCADE" in sql
