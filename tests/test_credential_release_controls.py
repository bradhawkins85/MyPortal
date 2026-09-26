from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import vault as routes
from app.repositories import credential_features


@pytest.mark.anyio
async def test_company_feature_flag_is_dark_by_default(monkeypatch):
    monkeypatch.setattr(credential_features.db, "fetch_one", AsyncMock(return_value=None))
    assert await credential_features.is_enabled(91) is False
    sql, params = credential_features.db.fetch_one.await_args.args
    assert "company_id = %s" in sql
    assert params == (91,)


@pytest.mark.anyio
async def test_disabled_company_fails_closed_before_membership(monkeypatch):
    monkeypatch.setattr(routes.feature_repo, "is_enabled", AsyncMock(return_value=False))
    membership = AsyncMock()
    monkeypatch.setattr(routes.user_company_repo, "get_user_company", membership)
    with pytest.raises(HTTPException) as denied:
        await routes._authorize({"id": 2, "is_super_admin": True}, 91, write=True)
    assert denied.value.status_code == 404
    membership.assert_not_awaited()


def test_release_runbook_covers_required_leakage_and_recovery_surfaces():
    text = Path("docs/operations/credential-rollout.md").read_text(encoding="utf-8")
    required = (
        "ticket and KB",
        "notifications/email",
        "analytics payloads",
        "audit/event JSON",
        "database-only restore",
        "Two simultaneous consumes",
        "offboarding",
        "Rollback is a flag update",
    )
    assert all(term in text for term in required)


def test_roadmap_records_all_eighteen_steps_in_order():
    text = Path("docs/roadmaps/itdoc-18-step-roadmap.md").read_text(encoding="utf-8")
    positions = [text.index(f"ITDOC {step:02d}/18") for step in range(1, 19)]
    assert positions == sorted(positions)
