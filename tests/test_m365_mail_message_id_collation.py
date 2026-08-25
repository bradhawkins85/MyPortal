from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "346_m365_graph_message_id_collation.sql"
)


def test_m365_graph_message_ids_use_case_sensitive_collation() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "alter table m365_mail_account_messages" in sql
    assert "modify message_uid varchar(512)" in sql
    assert "collate utf8mb4_bin" in sql
