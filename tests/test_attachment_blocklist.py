from __future__ import annotations

import hashlib
import asyncio

import pytest

from app.services import ticket_attachments


def test_content_hash_uses_sha256() -> None:
    contents = b"repeated email signature image"
    assert ticket_attachments.content_hash(contents) == hashlib.sha256(contents).hexdigest()


def test_ensure_not_blocked_rejects_matching_content(monkeypatch) -> None:
    async def blocked(digest: str) -> bool:
        assert digest == hashlib.sha256(b"signature").hexdigest()
        return True

    monkeypatch.setattr(ticket_attachments.blocklist_repo, "is_blocked", blocked)

    with pytest.raises(ValueError, match="blocklist.*discarded"):
        asyncio.run(ticket_attachments.ensure_not_blocked(b"signature"))


def test_ensure_not_blocked_allows_new_content(monkeypatch) -> None:
    async def not_blocked(_digest: str) -> bool:
        return False

    monkeypatch.setattr(ticket_attachments.blocklist_repo, "is_blocked", not_blocked)
    assert asyncio.run(ticket_attachments.ensure_not_blocked(b"new")) == hashlib.sha256(b"new").hexdigest()
