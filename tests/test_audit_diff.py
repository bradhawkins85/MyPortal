"""Unit tests for audit_diff helpers (diff + redaction)."""
from __future__ import annotations

from app.services.audit_diff import (
    REDACTED,
    diff,
    is_sensitive_key,
    redact,
    summarise_reply_body,
)


class TestIsSensitiveKey:
    def test_password_is_sensitive(self):
        assert is_sensitive_key("password") is True
        assert is_sensitive_key("PASSWORD") is True
        assert is_sensitive_key("user_password") is True

    def test_token_secret_keys_are_sensitive(self):
        for key in (
            "token",
            "refresh_token",
            "access_key",
            "client_secret",
            "api_key",
            "totp_secret",
            "encryption_key",
        ):
            assert is_sensitive_key(key) is True, key

    def test_normal_keys_are_not_sensitive(self):
        for key in ("name", "email", "id", "created_at", "company_id", "title"):
            assert is_sensitive_key(key) is False, key

    def test_non_string_keys_are_not_sensitive(self):
        assert is_sensitive_key(None) is False
        assert is_sensitive_key(123) is False


class TestRedact:
    def test_redacts_sensitive_top_level_fields(self):
        result = redact({"name": "alice", "password": "hunter2"})
        assert result == {"name": "alice", "password": REDACTED}

    def test_redacts_nested_sensitive_fields(self):
        result = redact({"user": {"email": "a@b", "client_secret": "x"}})
        assert result == {"user": {"email": "a@b", "client_secret": REDACTED}}

    def test_extra_sensitive_keys_are_honored(self):
        result = redact({"body": "<p>private</p>", "id": 1}, sensitive_extra_keys=("body",))
        assert result == {"body": REDACTED, "id": 1}

    def test_truncates_long_strings(self):
        long_value = "a" * 600
        result = redact({"description": long_value})
        assert result["description"].startswith("a" * 500)
        assert "[truncated]" in result["description"]

    def test_preserves_none_for_sensitive_fields(self):
        result = redact({"password": None, "name": "alice"})
        assert result == {"password": None, "name": "alice"}

    def test_handles_lists(self):
        result = redact([{"password": "p"}, {"name": "n"}])
        assert result == [{"password": REDACTED}, {"name": "n"}]


class TestDiff:
    def test_creation_returns_after_only(self):
        prev, new = diff(None, {"name": "alice", "email": "a@b"})
        assert prev is None
        assert new == {"name": "alice", "email": "a@b"}

    def test_deletion_returns_before_only(self):
        prev, new = diff({"name": "alice"}, None)
        assert prev == {"name": "alice"}
        assert new is None

    def test_returns_only_changed_fields(self):
        before = {"name": "alice", "email": "a@b", "status": "active"}
        after = {"name": "alice", "email": "alice@b", "status": "active"}
        prev, new = diff(before, after)
        assert prev == {"email": "a@b"}
        assert new == {"email": "alice@b"}

    def test_no_changes_returns_none(self):
        prev, new = diff({"name": "alice"}, {"name": "alice"})
        assert prev is None
        assert new is None

    def test_redacts_sensitive_changed_fields(self):
        before = {"password": "old"}
        after = {"password": "new"}
        prev, new = diff(before, after)
        assert prev == {"password": REDACTED}
        assert new == {"password": REDACTED}

    def test_extra_sensitive_keys_are_redacted_in_diff(self):
        prev, new = diff(
            None,
            {"id": 1, "body": "<p>secret</p>"},
            sensitive_extra_keys=("body",),
        )
        assert prev is None
        assert new == {"id": 1, "body": REDACTED}

    def test_handles_added_and_removed_fields(self):
        prev, new = diff({"a": 1}, {"b": 2})
        assert prev == {"a": 1, "b": None}
        assert new == {"a": None, "b": 2}

    def test_normalises_decimal_and_datetime(self):
        from datetime import datetime, timezone
        from decimal import Decimal

        before = {"amount": Decimal("10.00")}
        after = {"amount": Decimal("12.50"), "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc)}
        prev, new = diff(before, after)
        assert prev["amount"] == 10
        assert new["amount"] == 12.5
        assert new["updated_at"].startswith("2024-01-01")


class TestSummariseReplyBody:
    def test_empty_body_returns_zero_counts(self):
        assert summarise_reply_body("") == {"length": 0, "word_count": 0}
        assert summarise_reply_body(None) == {"length": 0, "word_count": 0}

    def test_strips_html_for_word_count(self):
        result = summarise_reply_body("<p>Hello there friend</p>")
        assert result["length"] == len("<p>Hello there friend</p>")
        assert result["word_count"] == 3
        assert result["text_length"] == len("Hello there friend")

    def test_summary_does_not_include_body(self):
        body = "<p>Sensitive customer reply with PII</p>"
        result = summarise_reply_body(body)
        assert "body" not in result
        assert body not in str(result)
