"""Tests for database parameter adaptation."""
from app.core.database import Database


def test_adapt_params_for_mysql_question_mark_placeholders():
    db = Database()
    sql, params = db._adapt_params_for_mysql(
        "INSERT INTO ticket_attachments (ticket_id, filename) VALUES (?, ?)",
        (1, "file"),
    )
    assert sql.endswith("VALUES (%s, %s)")
    assert params == (1, "file")


def test_adapt_params_for_mysql_question_mark_with_list_params():
    db = Database()
    sql, params = db._adapt_params_for_mysql(
        "SELECT * FROM ticket_attachments WHERE ticket_id = ? AND filename = ?",
        [5, "file.wav"],
    )
    assert "ticket_id = %s" in sql and "filename = %s" in sql
    assert params == [5, "file.wav"]


def test_adapt_params_for_mysql_leaves_existing_format_placeholders():
    db = Database()
    sql, params = db._adapt_params_for_mysql(
        "SELECT * FROM ticket_attachments WHERE ticket_id = %s AND filename = %s",
        (7, "file.wav"),
    )
    assert sql == "SELECT * FROM ticket_attachments WHERE ticket_id = %s AND filename = %s"
    assert params == (7, "file.wav")


def test_adapt_params_for_mysql_named_parameters():
    db = Database()
    sql, params = db._adapt_params_for_mysql(
        "SELECT * FROM ticket_attachments WHERE ticket_id = :ticket_id",
        {"ticket_id": 10},
    )
    assert sql == "SELECT * FROM ticket_attachments WHERE ticket_id = %(ticket_id)s"
    assert params == {"ticket_id": 10}


def test_adapt_params_for_mysql_none_params_returns_unchanged():
    db = Database()
    sql, params = db._adapt_params_for_mysql("SELECT * FROM ticket_attachments", None)
    assert sql == "SELECT * FROM ticket_attachments"
    assert params is None


def test_adapt_params_for_mysql_preserves_question_in_string_literal():
    db = Database()
    sql, params = db._adapt_params_for_mysql(
        "SELECT '?' AS literal_value, ticket_id FROM ticket_attachments WHERE ticket_id = ?",
        (8,),
    )
    assert "?' AS literal_value" in sql  # literal question mark preserved
    assert sql.endswith("ticket_id = %s")
    assert sql.count("%s") == 1
    assert params == (8,)


def test_adapt_params_for_mysql_preserves_question_in_comments():
    db = Database()
    sql, params = db._adapt_params_for_mysql(
        "SELECT * FROM ticket_attachments WHERE ticket_id = ? -- what about ?",
        (11,),
    )
    assert "ticket_id = %s -- what about ?" in sql
    assert params == (11,)
