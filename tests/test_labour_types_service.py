"""Tests for labour_types service – validation, CRUD logic, replace."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services import labour_types as labour_types_service
from app.repositories import labour_types as labour_types_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# _clean_code / _clean_name (private helpers tested via public surface)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_labour_type_strips_whitespace(monkeypatch):
    monkeypatch.setattr(
        labour_types_repo, "get_labour_type_by_code", AsyncMock(return_value=None)
    )
    created = {"id": 1, "code": "STD", "name": "Standard", "rate": None}
    monkeypatch.setattr(
        labour_types_repo, "create_labour_type", AsyncMock(return_value=created)
    )

    result = await labour_types_service.create_labour_type(
        code="  STD  ", name="  Standard  "
    )
    assert result["code"] == "STD"
    labour_types_repo.create_labour_type.assert_awaited_once_with(
        code="STD", name="Standard", rate=None
    )


@pytest.mark.anyio
async def test_create_labour_type_empty_code_raises(monkeypatch):
    with pytest.raises(ValueError, match="code is required"):
        await labour_types_service.create_labour_type(code="", name="Standard")


@pytest.mark.anyio
async def test_create_labour_type_whitespace_only_code_raises(monkeypatch):
    with pytest.raises(ValueError, match="code is required"):
        await labour_types_service.create_labour_type(code="   ", name="Standard")


@pytest.mark.anyio
async def test_create_labour_type_empty_name_raises(monkeypatch):
    with pytest.raises(ValueError, match="name is required"):
        await labour_types_service.create_labour_type(code="STD", name="")


@pytest.mark.anyio
async def test_create_labour_type_duplicate_code_raises(monkeypatch):
    monkeypatch.setattr(
        labour_types_repo,
        "get_labour_type_by_code",
        AsyncMock(return_value={"id": 5, "code": "STD"}),
    )

    with pytest.raises(ValueError, match="already exists"):
        await labour_types_service.create_labour_type(code="STD", name="Standard")


@pytest.mark.anyio
async def test_create_labour_type_with_rate(monkeypatch):
    monkeypatch.setattr(
        labour_types_repo, "get_labour_type_by_code", AsyncMock(return_value=None)
    )
    created = {"id": 2, "code": "PREM", "name": "Premium", "rate": 95.0}
    monkeypatch.setattr(
        labour_types_repo, "create_labour_type", AsyncMock(return_value=created)
    )

    result = await labour_types_service.create_labour_type(
        code="PREM", name="Premium", rate=95.0
    )
    assert result["rate"] == 95.0


# ---------------------------------------------------------------------------
# get_labour_type
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_labour_type_invalid_id_returns_none(monkeypatch):
    result = await labour_types_service.get_labour_type(0)
    assert result is None

    result = await labour_types_service.get_labour_type(-1)
    assert result is None


@pytest.mark.anyio
async def test_get_labour_type_delegates_to_repo(monkeypatch):
    expected = {"id": 3, "code": "STD", "name": "Standard", "rate": None}
    monkeypatch.setattr(
        labour_types_repo, "get_labour_type", AsyncMock(return_value=expected)
    )

    result = await labour_types_service.get_labour_type(3)
    assert result == expected


# ---------------------------------------------------------------------------
# list_labour_types
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_labour_types_returns_repo_results(monkeypatch):
    rows = [{"id": 1, "code": "A"}, {"id": 2, "code": "B"}]
    monkeypatch.setattr(
        labour_types_repo, "list_labour_types", AsyncMock(return_value=rows)
    )

    result = await labour_types_service.list_labour_types()
    assert result == rows


@pytest.mark.anyio
async def test_list_labour_types_db_not_initialised_returns_empty(monkeypatch):
    async def raise_pool_error():
        raise RuntimeError("Database pool not initialised")

    monkeypatch.setattr(labour_types_repo, "list_labour_types", raise_pool_error)

    result = await labour_types_service.list_labour_types()
    assert result == []


@pytest.mark.anyio
async def test_list_labour_types_other_runtime_error_propagates(monkeypatch):
    async def raise_other():
        raise RuntimeError("Something else went wrong")

    monkeypatch.setattr(labour_types_repo, "list_labour_types", raise_other)

    with pytest.raises(RuntimeError, match="Something else"):
        await labour_types_service.list_labour_types()


# ---------------------------------------------------------------------------
# update_labour_type
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_labour_type_invalid_id_returns_none(monkeypatch):
    result = await labour_types_service.update_labour_type(0, name="New")
    assert result is None


@pytest.mark.anyio
async def test_update_labour_type_empty_code_raises(monkeypatch):
    with pytest.raises(ValueError, match="code is required"):
        await labour_types_service.update_labour_type(1, code="  ")


@pytest.mark.anyio
async def test_update_labour_type_duplicate_code_raises(monkeypatch):
    monkeypatch.setattr(
        labour_types_repo,
        "get_labour_type_by_code",
        AsyncMock(return_value={"id": 99, "code": "STD"}),
    )

    with pytest.raises(ValueError, match="already exists"):
        await labour_types_service.update_labour_type(1, code="STD")


@pytest.mark.anyio
async def test_update_labour_type_same_code_no_conflict(monkeypatch):
    existing = {"id": 1, "code": "STD", "name": "Standard"}
    monkeypatch.setattr(
        labour_types_repo,
        "get_labour_type_by_code",
        AsyncMock(return_value=existing),
    )
    updated = {"id": 1, "code": "STD", "name": "Standard Updated"}
    monkeypatch.setattr(
        labour_types_repo, "update_labour_type", AsyncMock(return_value=updated)
    )

    result = await labour_types_service.update_labour_type(1, code="STD")
    assert result["name"] == "Standard Updated"


@pytest.mark.anyio
async def test_update_labour_type_empty_name_raises(monkeypatch):
    with pytest.raises(ValueError, match="name is required"):
        await labour_types_service.update_labour_type(1, name="")


@pytest.mark.anyio
async def test_update_labour_type_no_changes_returns_current(monkeypatch):
    current = {"id": 1, "code": "STD", "name": "Standard"}
    monkeypatch.setattr(
        labour_types_repo, "get_labour_type", AsyncMock(return_value=current)
    )

    result = await labour_types_service.update_labour_type(1)
    assert result == current


# ---------------------------------------------------------------------------
# delete_labour_type
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_labour_type_invalid_id_is_noop(monkeypatch):
    delete_mock = AsyncMock()
    monkeypatch.setattr(labour_types_repo, "delete_labour_type", delete_mock)

    await labour_types_service.delete_labour_type(0)
    delete_mock.assert_not_awaited()

    await labour_types_service.delete_labour_type(-5)
    delete_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_delete_labour_type_valid_id_delegates(monkeypatch):
    delete_mock = AsyncMock()
    monkeypatch.setattr(labour_types_repo, "delete_labour_type", delete_mock)

    await labour_types_service.delete_labour_type(3)
    delete_mock.assert_awaited_once_with(3)


# ---------------------------------------------------------------------------
# replace_labour_types
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_replace_labour_types_cleans_definitions(monkeypatch):
    captured: list[dict] = []

    async def fake_replace(definitions):
        captured.extend(definitions)
        return definitions

    monkeypatch.setattr(labour_types_repo, "replace_labour_types", fake_replace)

    await labour_types_service.replace_labour_types(
        [
            {"code": "  STD  ", "name": "  Standard  ", "rate": 80.0},
            {"code": "ADV", "name": "Advanced", "id": "3"},
        ]
    )

    assert captured[0]["code"] == "STD"
    assert captured[0]["name"] == "Standard"
    assert captured[1]["id"] == 3  # coerced from string


@pytest.mark.anyio
async def test_replace_labour_types_invalid_id_becomes_none(monkeypatch):
    captured: list[dict] = []

    async def fake_replace(definitions):
        captured.extend(definitions)
        return definitions

    monkeypatch.setattr(labour_types_repo, "replace_labour_types", fake_replace)

    await labour_types_service.replace_labour_types(
        [{"code": "X", "name": "X", "id": "bad-id"}]
    )

    assert captured[0]["id"] is None
