"""Tests for the centralised audit.record helper.

Verifies field-level diffing, secret redaction, request_id propagation, and
context-var fallbacks for user_id.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

import pytest

from app.core import logging as logging_module
from app.services import audit
from app.services.audit_diff import REDACTED


class _CapturingRepo:
    """Stand-in for app.repositories.audit_logs that records create calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_audit_log(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


@contextmanager
def _patched_repo(monkeypatch: pytest.MonkeyPatch) -> _CapturingRepo:
    repo = _CapturingRepo()
    monkeypatch.setattr(audit, "audit_repo", repo)
    yield repo


@pytest.mark.asyncio
async def test_record_skips_no_op_updates(monkeypatch):
    with _patched_repo(monkeypatch) as repo:
        await audit.record(
            action="thing.update",
            entity_type="thing",
            entity_id=1,
            before={"name": "a"},
            after={"name": "a"},
        )
    assert repo.calls == []


@pytest.mark.asyncio
async def test_record_captures_only_changed_fields(monkeypatch):
    with _patched_repo(monkeypatch) as repo:
        await audit.record(
            action="thing.update",
            user_id=42,
            entity_type="thing",
            entity_id=1,
            before={"name": "a", "status": "open"},
            after={"name": "a", "status": "closed"},
        )
    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert call["previous_value"] == {"status": "open"}
    assert call["new_value"] == {"status": "closed"}


@pytest.mark.asyncio
async def test_record_redacts_passwords(monkeypatch):
    with _patched_repo(monkeypatch) as repo:
        await audit.record(
            action="user.update",
            user_id=1,
            entity_type="user",
            entity_id=2,
            before={"password_hash": "old", "email": "a@b"},
            after={"password_hash": "new", "email": "a@b"},
        )
    call = repo.calls[0]
    assert call["previous_value"] == {"password_hash": REDACTED}
    assert call["new_value"] == {"password_hash": REDACTED}


@pytest.mark.asyncio
async def test_record_redacts_metadata_secrets(monkeypatch):
    with _patched_repo(monkeypatch) as repo:
        await audit.record(
            action="integration.configure",
            user_id=1,
            entity_type="integration",
            entity_id=10,
            metadata={"client_secret": "abc", "name": "M365"},
        )
    metadata = repo.calls[0]["metadata"]
    assert metadata == {"client_secret": REDACTED, "name": "M365"}


@pytest.mark.asyncio
async def test_record_never_stores_ticket_reply_body(monkeypatch):
    """Regression: ticket replies must never persist the body in audit_logs."""

    secret_body = "<p>Top secret customer reply with PII</p>"
    with _patched_repo(monkeypatch) as repo:
        await audit.record(
            action="ticket.replied",
            user_id=1,
            entity_type="ticket",
            entity_id=99,
            metadata={
                "reply_id": 7,
                "channel": "public",
                "body": secret_body,
            },
            sensitive_extra_keys=("body",),
        )
    serialised = repr(repo.calls[0])
    assert secret_body not in serialised
    assert repo.calls[0]["metadata"]["body"] == REDACTED


@pytest.mark.asyncio
async def test_record_propagates_request_id_from_context(monkeypatch):
    with _patched_repo(monkeypatch) as repo:
        tokens = logging_module.set_request_context(request_id="req-abc-123")
        try:
            await audit.record(
                action="thing.create",
                user_id=1,
                entity_type="thing",
                entity_id=1,
                before=None,
                after={"name": "x"},
            )
        finally:
            logging_module.reset_request_context(tokens)
    assert repo.calls[0]["request_id"] == "req-abc-123"


@pytest.mark.asyncio
async def test_record_pulls_user_id_from_context_when_not_provided(monkeypatch):
    with _patched_repo(monkeypatch) as repo:
        tokens = logging_module.set_request_context(user_id=77)
        try:
            await audit.record(
                action="thing.create",
                entity_type="thing",
                entity_id=1,
                before=None,
                after={"name": "x"},
            )
        finally:
            logging_module.reset_request_context(tokens)
    assert repo.calls[0]["user_id"] == 77


@pytest.mark.asyncio
async def test_record_swallows_db_failures(monkeypatch):
    """If the repo raises, the request must not break."""

    class _BoomRepo:
        async def create_audit_log(self, **kwargs):
            raise RuntimeError("db down")

    monkeypatch.setattr(audit, "audit_repo", _BoomRepo())
    # Should not raise
    await audit.record(
        action="thing.update",
        user_id=1,
        entity_type="thing",
        entity_id=1,
        before={"a": 1},
        after={"a": 2},
    )
