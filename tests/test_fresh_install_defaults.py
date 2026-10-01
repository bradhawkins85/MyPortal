"""Regression tests for problems that blocked a fresh installation."""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values
from fastapi import HTTPException

from app.api.routes import auth as auth_routes
from app.core.config import Settings

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_env_example_produces_valid_settings(monkeypatch):
    """The installers copy .env.example verbatim; it must load without edits."""

    values = dotenv_values(_PROJECT_ROOT / ".env.example", interpolate=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value or "")

    settings = Settings(_env_file=None)

    assert settings.wan_ip_source_url is None


@pytest.mark.anyio("asyncio")
async def test_first_user_uses_existing_company_when_none_requested(monkeypatch):
    async def list_companies(include_archived: bool = False):
        assert include_archived is True
        return [{"id": 7}, {"id": 3}]

    async def create_company(**_: object):  # pragma: no cover - must not run
        raise AssertionError("an existing company should be reused")

    monkeypatch.setattr(auth_routes.company_repo, "list_companies", list_companies)
    monkeypatch.setattr(auth_routes.company_repo, "create_company", create_company)

    assert await auth_routes._resolve_first_user_company_id(None) == 3


@pytest.mark.anyio("asyncio")
async def test_first_user_creates_company_on_empty_database(monkeypatch):
    created: dict[str, object] = {}

    async def list_companies(include_archived: bool = False):
        return []

    async def create_company(**data: object):
        created.update(data)
        return {"id": 1, **data}

    monkeypatch.setattr(auth_routes.company_repo, "list_companies", list_companies)
    monkeypatch.setattr(auth_routes.company_repo, "create_company", create_company)

    assert await auth_routes._resolve_first_user_company_id(None) == 1
    assert created == {"name": "Default Company"}


@pytest.mark.anyio("asyncio")
async def test_first_user_rejects_unknown_company(monkeypatch):
    async def get_company_by_id(company_id: int):
        return None

    monkeypatch.setattr(auth_routes.company_repo, "get_company_by_id", get_company_by_id)

    with pytest.raises(HTTPException) as excinfo:
        await auth_routes._resolve_first_user_company_id(42)
    assert excinfo.value.status_code == 400
