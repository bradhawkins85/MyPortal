from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.repositories import standing_credential_grants as grants


def test_job_title_normalization_is_unicode_and_whitespace_stable():
    assert grants.normalize_job_title("  Operations\t Manager ") == "operations manager"
    assert grants.normalize_job_title("ＯＰＥＲＡＴＩＯＮＳ Manager") == "operations manager"
    assert grants.normalize_job_title("Straße Manager") == "strasse manager"


@pytest.mark.anyio
async def test_eligible_staff_filters_titles_after_secure_database_scope(monkeypatch):
    monkeypatch.setattr(
        grants.db,
        "fetch_all",
        AsyncMock(return_value=[
            {"staff_id": 1, "user_id": 10, "first_name": "A", "last_name": "One", "job_title": "Operations  Manager", "email": "a@example.test"},
            {"staff_id": 2, "user_id": 11, "first_name": "B", "last_name": "Two", "job_title": "General Manager", "email": "b@example.test"},
        ]),
    )
    rows = await grants.eligible_staff(7, " operations manager ")
    assert [row["staff_id"] for row in rows] == [1]
    sql, params = grants.db.fetch_all.await_args.args
    assert "s.company_id = %s" in sql
    assert "s.portal_user_id" in sql
    assert "email_verified_at IS NOT NULL" in sql
    assert "NOT EXISTS" in sql
    assert params == (7,)


@pytest.mark.anyio
async def test_reveal_does_not_decrypt_when_concurrent_revoke_wins(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(grants, "resolve", AsyncMock(return_value=[{
        "id": 4, "credential_id": 9, "current_version": 3,
        "expires_at": now + timedelta(days=1),
    }]))
    monkeypatch.setattr(grants.db, "execute_rowcount", AsyncMock(return_value=0))
    reveal = AsyncMock()
    monkeypatch.setattr(grants.vault, "reveal", reveal)
    assert await grants.reveal(2, 7, 9) is None
    reveal.assert_not_awaited()


@pytest.mark.anyio
async def test_reveal_uses_current_secret_version_and_can_repeat(monkeypatch):
    row = {"id": 4, "credential_id": 9, "current_version": 6}
    monkeypatch.setattr(grants, "resolve", AsyncMock(return_value=[row]))
    monkeypatch.setattr(grants.db, "execute_rowcount", AsyncMock(return_value=1))
    monkeypatch.setattr(grants.vault, "reveal", AsyncMock(return_value=(6, "secret")))
    assert await grants.reveal(2, 7, 9) == (row, "secret")
    assert await grants.reveal(2, 7, 9) == (row, "secret")
    assert grants.vault.reveal.await_count == 2
