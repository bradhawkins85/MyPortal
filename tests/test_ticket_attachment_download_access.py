"""Regression tests: ticket attachment downloads require ticket access."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import tickets as tickets_routes

TICKET = {"id": 10, "requester_id": 1, "company_id": 4}


@pytest.fixture
def attachment_env(monkeypatch, tmp_path):
    stored = tmp_path / "stored.bin"
    stored.write_bytes(b"data")
    monkeypatch.setattr(
        tickets_routes.attachments_repo,
        "get_attachment",
        AsyncMock(
            return_value={
                "id": 3,
                "ticket_id": 10,
                "access_level": "open",
                "filename": "stored.bin",
                "original_filename": "file.txt",
                "mime_type": "text/plain",
            }
        ),
    )
    monkeypatch.setattr(tickets_routes.tickets_repo, "get_ticket", AsyncMock(return_value=dict(TICKET)))
    monkeypatch.setattr(tickets_routes.tickets_repo, "is_ticket_watcher", AsyncMock(return_value=False))
    monkeypatch.setattr(tickets_routes, "_has_helpdesk_permission", AsyncMock(return_value=False))
    monkeypatch.setattr(
        tickets_routes.attachments_service, "get_attachment_file_path", lambda _name: stored
    )


@pytest.mark.asyncio
async def test_unrelated_user_cannot_download_open_attachment(monkeypatch, attachment_env):
    monkeypatch.setattr(tickets_routes.user_company_repo, "get_user_company", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await tickets_routes.download_ticket_attachment(10, 3, current_user={"id": 99}, preview=False)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_requester_can_download_open_attachment(attachment_env):
    response = await tickets_routes.download_ticket_attachment(10, 3, current_user={"id": 1}, preview=False)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_company_ticket_manager_can_download_open_attachment(monkeypatch, attachment_env):
    monkeypatch.setattr(
        tickets_routes.user_company_repo,
        "get_user_company",
        AsyncMock(return_value={"user_id": 99, "company_id": 4, "can_manage_tickets": True, "permissions": {"menu.tickets": "write"}, "menu_permissions": {"menu.tickets": "write"}}),
    )
    from app import main as main_module

    monkeypatch.setattr(main_module, "_membership_menu_can", lambda user, membership, key, write=False: key == "menu.tickets")
    response = await tickets_routes.download_ticket_attachment(10, 3, current_user={"id": 99}, preview=False)
    assert response.status_code == 200
