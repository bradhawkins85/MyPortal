from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.main import _pop_pkce_verifier, _pkce_verifier_store, _store_pkce_verifier


@pytest.mark.anyio("asyncio")
async def test_store_and_pop_pkce_verifier_is_one_time() -> None:
    _pkce_verifier_store.clear()

    handle = await _store_pkce_verifier("verifier-123")
    assert isinstance(handle, str) and handle

    assert await _pop_pkce_verifier(handle) == "verifier-123"
    assert await _pop_pkce_verifier(handle) is None


@pytest.mark.anyio("asyncio")
async def test_pop_pkce_verifier_rejects_expired_handle() -> None:
    _pkce_verifier_store.clear()

    _pkce_verifier_store["expired"] = (
        "verifier-123",
        datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    assert await _pop_pkce_verifier("expired") is None
