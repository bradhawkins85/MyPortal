"""Regression tests for the Matrix chat auto-assign service.

Auto-assign rules previously only stamped ``assigned_tech_user_id`` on the
room and never invited the technician into the Matrix room, so matching rules
appeared to do nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.repositories import chat as chat_repo
from app.repositories import chat_auto_assign as rules_repo
from app.repositories import users as users_repo
from app.services import chat_auto_assign
from app.services import matrix as matrix_service


DEFAULT_RULE = {
    "id": 1,
    "name": "Default",
    "priority": 10,
    "is_active": True,
    "is_default": True,
    "assigned_tech_user_id": 7,
    "conditions": [],
}


@pytest.fixture
def mocks(monkeypatch):
    m = {
        "list_rules": AsyncMock(return_value=[DEFAULT_RULE]),
        "assign_tech": AsyncMock(),
        "get_room": AsyncMock(return_value={"id": 42, "matrix_room_id": "!room:example.org"}),
        "add_participant": AsyncMock(),
        "get_user_by_id": AsyncMock(return_value={"id": 7, "matrix_user_id": "@brad:example.org"}),
        "invite_user": AsyncMock(return_value={}),
        "set_user_power_level": AsyncMock(return_value={}),
    }
    monkeypatch.setattr(rules_repo, "list_rules", m["list_rules"])
    monkeypatch.setattr(chat_repo, "assign_tech", m["assign_tech"])
    monkeypatch.setattr(chat_repo, "get_room", m["get_room"])
    monkeypatch.setattr(chat_repo, "add_participant", m["add_participant"])
    monkeypatch.setattr(users_repo, "get_user_by_id", m["get_user_by_id"])
    monkeypatch.setattr(matrix_service, "invite_user", m["invite_user"])
    monkeypatch.setattr(matrix_service, "set_user_power_level", m["set_user_power_level"])
    return m


@pytest.mark.asyncio
async def test_default_rule_invites_technician_to_matrix_room(mocks):
    result = await chat_auto_assign.apply_auto_assign(42, company_name="Acme", subject="Help")

    assert result == 7
    mocks["assign_tech"].assert_awaited_once_with(42, 7)
    mocks["invite_user"].assert_awaited_once_with("!room:example.org", "@brad:example.org")
    mocks["set_user_power_level"].assert_awaited_once_with("!room:example.org", "@brad:example.org", 100)
    mocks["add_participant"].assert_awaited_once_with(
        42, "@brad:example.org", role="technician", user_id=7
    )


@pytest.mark.asyncio
async def test_technician_without_matrix_id_is_assigned_but_not_invited(mocks):
    mocks["get_user_by_id"].return_value = {"id": 7, "matrix_user_id": None}

    result = await chat_auto_assign.apply_auto_assign(42)

    assert result == 7
    mocks["assign_tech"].assert_awaited_once_with(42, 7)
    mocks["invite_user"].assert_not_awaited()
    mocks["add_participant"].assert_not_awaited()


@pytest.mark.asyncio
async def test_invite_failure_does_not_undo_assignment(mocks):
    mocks["invite_user"].side_effect = RuntimeError("matrix down")

    result = await chat_auto_assign.apply_auto_assign(42)

    assert result == 7
    mocks["assign_tech"].assert_awaited_once_with(42, 7)
    mocks["add_participant"].assert_awaited_once()


@pytest.mark.asyncio
async def test_room_already_assigned_to_someone_else_is_not_invited(mocks):
    mocks["get_room"].return_value = {
        "id": 42,
        "matrix_room_id": "!room:example.org",
        "assigned_tech_user_id": 99,
    }

    result = await chat_auto_assign.apply_auto_assign(42)

    assert result is None
    mocks["invite_user"].assert_not_awaited()
