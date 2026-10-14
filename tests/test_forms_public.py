"""Tests for the public-form feature.

A form can be flagged ``is_public`` so that it is visible to *every* portal user
without a per-user/company ``form_permissions`` row. These tests cover the
repository query change, boolean normalisation, create/update persistence, the
schema defaults, and the admin checkbox value parser.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core import database
from app.repositories import forms as forms_repo


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[dict[str, Any]] = []
        self.lastrowid = 999

    async def __aenter__(self) -> "_FakeCursor":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, query: str, params: Any = None) -> None:
        self.executed.append({"query": query, "params": params})

    async def executemany(self, query: str, params: Any) -> None:
        self.executed.append({"query": query, "params": params})


class _FakeConn:
    def __init__(self) -> None:
        self.cursor_obj = _FakeCursor()

    def cursor(self, *args: Any, **kwargs: Any) -> _FakeCursor:
        return self.cursor_obj


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.mark.asyncio
async def test_list_forms_for_user_includes_public_and_assigned(monkeypatch):
    """The per-user query must return public forms *and* assigned forms."""
    captured: dict[str, Any] = {}

    async def fake_fetch_all(query: str, params: Any = None) -> list[dict[str, Any]]:
        captured["query"] = query
        captured["params"] = params
        return [
            {"id": 1, "name": "A", "url": "https://forms.example.com/a", "is_public": 1},
            {"id": 2, "name": "B", "url": "https://forms.example.com/b", "is_public": 0},
        ]

    monkeypatch.setattr(database.db, "fetch_all", fake_fetch_all)

    rows = await forms_repo.list_forms_for_user(42)

    assert captured["params"] == (42,)
    # LEFT JOIN so a form with no permission row still surfaces when it is public.
    assert "LEFT JOIN form_permissions" in captured["query"]
    # Public forms OR explicitly assigned forms.
    assert "f.is_public = 1 OR fp.user_id IS NOT NULL" in captured["query"]
    # is_public must be normalised to a real bool.
    assert rows[0]["is_public"] is True
    assert rows[1]["is_public"] is False


@pytest.mark.asyncio
async def test_create_form_persists_is_public(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(database.db, "acquire", lambda: _FakeAcquire(conn))

    async def fake_fetch_one(query: str, params: Any = None) -> dict[str, Any] | None:
        return {"id": 999, "name": "New", "url": "https://forms.example.com/x", "is_public": 1}

    monkeypatch.setattr(database.db, "fetch_one", fake_fetch_one)

    await forms_repo.create_form(
        name="New",
        url="https://forms.example.com/x",
        embed_code=None,
        description=None,
        is_public=True,
    )

    insert = conn.cursor_obj.executed[0]
    assert "INSERT INTO forms" in insert["query"]
    assert "is_public" in insert["query"]
    # The boolean is stored as a 0/1 integer.
    assert insert["params"][4] == 1


@pytest.mark.asyncio
async def test_create_form_default_is_private(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(database.db, "acquire", lambda: _FakeAcquire(conn))

    async def fake_fetch_one(query: str, params: Any = None) -> dict[str, Any] | None:
        return {"id": 999, "name": "New", "url": "https://forms.example.com/x", "is_public": 0}

    monkeypatch.setattr(database.db, "fetch_one", fake_fetch_one)

    await forms_repo.create_form(
        name="New",
        url="https://forms.example.com/x",
        embed_code=None,
        description=None,
    )

    insert = conn.cursor_obj.executed[0]
    assert insert["params"][4] == 0


@pytest.mark.asyncio
async def test_update_form_persists_is_public(monkeypatch):
    updates: list[dict[str, Any]] = []

    async def fake_execute(query: str, params: Any = None) -> int:
        updates.append({"query": query, "params": params})
        return 1

    async def fake_fetch_one(query: str, params: Any = None) -> dict[str, Any] | None:
        return {"id": 7, "name": "Upd", "url": "https://forms.example.com/u", "is_public": 1}

    monkeypatch.setattr(database.db, "execute", fake_execute)
    monkeypatch.setattr(database.db, "fetch_one", fake_fetch_one)

    result = await forms_repo.update_form(
        7,
        name="Upd",
        url="https://forms.example.com/u",
        embed_code=None,
        description=None,
        is_public=True,
    )

    update = updates[0]
    assert "UPDATE forms" in update["query"]
    assert "is_public = %s" in update["query"]
    assert update["params"][4] == 1  # is_public stored as int
    assert update["params"][5] == 7  # form_id
    assert result["is_public"] is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, False),
        ("", False),
        ("0", False),
        ("1", True),
        ("on", True),
        ("yes", True),
        ("true", True),
        ("TRUE", True),
    ],
)
def test_parse_bool_parses_checkbox_values(raw: str | None, expected: bool) -> None:
    from app.main import _parse_bool

    assert _parse_bool(raw) is expected


def test_schema_default_and_override() -> None:
    from app.schemas.forms import FormCreate, FormUpdate, FormResponse

    # Creating without specifying is_public defaults to private.
    assert FormCreate(name="N", url="https://forms.example.com/x").is_public is False
    # Creating with is_public=True.
    assert FormCreate(name="N", url="https://forms.example.com/x", is_public=True).is_public is True

    # Update omits is_public -> None (meaning "leave unchanged").
    assert FormUpdate().is_public is None
    # Update explicitly sets is_public.
    assert FormUpdate(is_public=True).is_public is True

    # FormResponse accepts and exposes is_public.
    assert FormResponse(
        id=1, name="N", url="https://forms.example.com/x", is_public=True
    ).is_public is True

