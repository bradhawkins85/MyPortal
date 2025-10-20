import hashlib
from datetime import datetime, timezone

import pytest

from app.services.mcp import chatgpt as chatgpt_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _hashed(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@pytest.mark.anyio("asyncio")
async def test_handle_rpc_list_tools(monkeypatch):
    module_record = {
        "slug": "chatgpt-mcp",
        "enabled": True,
        "settings": {
            "shared_secret_hash": _hashed("secret"),
            "allowed_actions": ["listTickets", "getTicket"],
            "max_results": 25,
            "allow_ticket_updates": False,
            "allowed_statuses": ["open", "pending"],
        },
    }

    async def fake_get_module(slug: str):
        assert slug == "chatgpt-mcp"
        return module_record

    monkeypatch.setattr(chatgpt_service.module_repo, "get_module", fake_get_module)

    response = await chatgpt_service.handle_rpc_request(
        {"jsonrpc": "2.0", "id": 1, "method": "listTools"},
        "Bearer secret",
    )

    assert response["result"]["tools"]
    tool_names = {tool["name"] for tool in response["result"]["tools"]}
    assert tool_names == {"listTickets", "getTicket"}


@pytest.mark.anyio("asyncio")
async def test_handle_rpc_call_tool_list_tickets(monkeypatch):
    now = datetime.now(timezone.utc)
    module_record = {
        "slug": "chatgpt-mcp",
        "enabled": True,
        "settings": {
            "shared_secret_hash": _hashed("secret"),
            "allowed_actions": ["listTickets"],
            "max_results": 50,
            "allow_ticket_updates": False,
            "allowed_statuses": ["open"],
        },
    }

    async def fake_get_module(slug: str):
        return module_record

    async def fake_list_tickets(**kwargs):
        return [
            {
                "id": 1,
                "subject": "Example",
                "status": "open",
                "priority": "normal",
                "module_slug": None,
                "company_id": 5,
                "requester_id": 9,
                "assigned_user_id": None,
                "created_at": now,
                "updated_at": now,
                "closed_at": None,
                "description": "Test",
                "category": None,
                "external_reference": None,
            }
        ]

    monkeypatch.setattr(chatgpt_service.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(chatgpt_service.tickets_repo, "list_tickets", fake_list_tickets)

    response = await chatgpt_service.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "callTool",
            "params": {"name": "listTickets", "arguments": {"status": "open", "limit": 5}},
        },
        "Bearer secret",
    )

    payload = response["result"]["content"][0]["data"]
    assert payload["limit"] == 5
    assert payload["tickets"][0]["subject"] == "Example"
    assert payload["tickets"][0]["updated_at"].endswith("Z") or "+" in payload["tickets"][0]["updated_at"]


@pytest.mark.anyio("asyncio")
async def test_handle_rpc_update_ticket_blocked(monkeypatch):
    module_record = {
        "slug": "chatgpt-mcp",
        "enabled": True,
        "settings": {
            "shared_secret_hash": _hashed("secret"),
            "allowed_actions": ["updateTicket"],
            "max_results": 10,
            "allow_ticket_updates": False,
            "allowed_statuses": ["open"],
        },
    }

    async def fake_get_module(slug: str):
        return module_record

    monkeypatch.setattr(chatgpt_service.module_repo, "get_module", fake_get_module)

    with pytest.raises(chatgpt_service.ChatGPTMCPError) as exc:
        await chatgpt_service.handle_rpc_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "callTool",
                "params": {"name": "updateTicket", "arguments": {"ticket_id": 1, "status": "open"}},
            },
            "Bearer secret",
        )

    assert exc.value.status_code == 403


@pytest.mark.anyio("asyncio")
async def test_handle_rpc_invalid_token(monkeypatch):
    module_record = {
        "slug": "chatgpt-mcp",
        "enabled": True,
        "settings": {
            "shared_secret_hash": _hashed("secret"),
            "allowed_actions": ["listTickets"],
            "max_results": 50,
            "allow_ticket_updates": False,
            "allowed_statuses": ["open"],
        },
    }

    async def fake_get_module(slug: str):
        return module_record

    monkeypatch.setattr(chatgpt_service.module_repo, "get_module", fake_get_module)

    with pytest.raises(chatgpt_service.ChatGPTMCPError) as exc:
        await chatgpt_service.handle_rpc_request(
            {"jsonrpc": "2.0", "id": 4, "method": "listTools"},
            "Bearer wrong",
        )

    assert exc.value.status_code == 401
