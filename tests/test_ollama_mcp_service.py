import hashlib
from datetime import datetime, timezone

import pytest

from app.services.mcp import ollama as ollama_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _stub_emit_ticket_events(monkeypatch):
    async def fake_emit_event(*args, **kwargs):
        return None

    monkeypatch.setattr(
        ollama_service.tickets_service,
        "emit_ticket_updated_event",
        fake_emit_event,
    )


def _hashed(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _module(**overrides):
    settings = {
        "shared_secret_hash": _hashed("secret"),
        "allowed_actions": list(ollama_service.DEFAULT_TOOLS),
        "max_results": 25,
        "allow_ticket_replies": False,
        "allow_ticket_updates": False,
        "allowed_statuses": ["open", "pending"],
        "system_user_id": None,
        "include_internal_replies": False,
        "server_name": "MyPortal Ollama MCP",
        "server_version": "1.0.0",
    }
    settings.update(overrides.get("settings", {}))
    return {
        "slug": "ollama-mcp",
        "enabled": overrides.get("enabled", True),
        "settings": settings,
    }


def _patch_module(monkeypatch, record):
    async def fake_get_module(slug):
        assert slug == "ollama-mcp"
        return record

    monkeypatch.setattr(ollama_service.module_repo, "get_module", fake_get_module)


@pytest.mark.anyio("asyncio")
async def test_initialize_returns_protocol_and_capabilities(monkeypatch):
    _patch_module(monkeypatch, _module())

    response = await ollama_service.handle_rpc_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        "Bearer secret",
    )

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    result = response["result"]
    assert result["protocolVersion"] == ollama_service.PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "MyPortal Ollama MCP"
    assert result["capabilities"]["tools"]["listChanged"] is False


@pytest.mark.anyio("asyncio")
async def test_notifications_initialized_returns_none(monkeypatch):
    _patch_module(monkeypatch, _module())

    response = await ollama_service.handle_rpc_request(
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        "Bearer secret",
    )

    assert response is None


@pytest.mark.anyio("asyncio")
async def test_tools_list_respects_allowed_actions(monkeypatch):
    _patch_module(
        monkeypatch,
        _module(settings={"allowed_actions": ["search_tickets", "get_ticket"]}),
    )

    response = await ollama_service.handle_rpc_request(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        "Bearer secret",
    )

    names = {tool["name"] for tool in response["result"]["tools"]}
    assert names == {"search_tickets", "get_ticket"}


@pytest.mark.anyio("asyncio")
async def test_search_tickets_requires_query(monkeypatch):
    _patch_module(monkeypatch, _module())

    with pytest.raises(ollama_service.OllamaMCPError) as exc:
        await ollama_service.handle_rpc_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "search_tickets", "arguments": {}},
            },
            "Bearer secret",
        )
    assert exc.value.status_code == 400
    assert exc.value.rpc_code == ollama_service.ERR_INVALID_PARAMS


@pytest.mark.anyio("asyncio")
async def test_search_tickets_returns_text_content(monkeypatch):
    now = datetime.now(timezone.utc)
    _patch_module(monkeypatch, _module())

    captured = {}

    async def fake_list_tickets(**kwargs):
        captured.update(kwargs)
        return [
            {
                "id": 7,
                "subject": "Voicemail follow-up",
                "description": "voicemail from 61 4xx",
                "status": "open",
                "priority": "normal",
                "module_slug": None,
                "company_id": 5,
                "requester_id": 9,
                "assigned_user_id": None,
                "created_at": now,
                "updated_at": now,
                "closed_at": None,
                "category": None,
                "external_reference": None,
                # A sensitive-looking field that should be filtered out.
                "api_key": "should-not-appear",
            }
        ]

    monkeypatch.setattr(
        ollama_service.tickets_repo, "list_tickets", fake_list_tickets
    )

    response = await ollama_service.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "search_tickets",
                "arguments": {"query": "voicemail", "limit": 5},
            },
        },
        "Bearer secret",
    )

    assert captured["search"] == "voicemail"
    assert captured["limit"] == 5
    result = response["result"]
    # Spec-compliant content array.
    assert result["content"][0]["type"] == "text"
    assert "voicemail" in result["content"][0]["text"].lower()
    structured = result["structuredContent"]
    assert structured["query"] == "voicemail"
    assert structured["count"] == 1
    ticket = structured["tickets"][0]
    assert ticket["subject"] == "Voicemail follow-up"
    assert "api_key" not in ticket
    assert result["isError"] is False


@pytest.mark.anyio("asyncio")
async def test_search_tickets_caps_limit_to_max_results(monkeypatch):
    _patch_module(monkeypatch, _module(settings={"max_results": 10}))

    captured = {}

    async def fake_list_tickets(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        ollama_service.tickets_repo, "list_tickets", fake_list_tickets
    )

    await ollama_service.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "search_tickets",
                "arguments": {"query": "hi", "limit": 999},
            },
        },
        "Bearer secret",
    )

    assert captured["limit"] == 10  # Capped at max_results.


@pytest.mark.anyio("asyncio")
async def test_get_ticket_returns_replies_and_watchers(monkeypatch):
    now = datetime.now(timezone.utc)
    _patch_module(monkeypatch, _module())

    async def fake_get_ticket(ticket_id):
        assert ticket_id == 42
        return {
            "id": 42,
            "subject": "Hello",
            "description": "World",
            "status": "open",
            "priority": "normal",
            "module_slug": None,
            "company_id": 1,
            "requester_id": 2,
            "assigned_user_id": None,
            "external_reference": None,
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
            "category": None,
        }

    async def fake_list_replies(ticket_id, *, include_internal):
        assert ticket_id == 42
        # Default settings have include_internal_replies=False.
        assert include_internal is False
        return [{"id": 1, "ticket_id": 42, "author_id": 9, "body": "hi", "is_internal": 0, "created_at": now}]

    async def fake_list_watchers(ticket_id):
        assert ticket_id == 42
        return [{"id": 5, "ticket_id": 42, "user_id": 9, "created_at": now}]

    monkeypatch.setattr(ollama_service.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(ollama_service.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(
        ollama_service.tickets_repo, "list_watchers", fake_list_watchers
    )

    response = await ollama_service.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "get_ticket", "arguments": {"ticket_id": 42}},
        },
        "Bearer secret",
    )

    structured = response["result"]["structuredContent"]
    assert structured["ticket"]["id"] == 42
    assert len(structured["replies"]) == 1
    assert len(structured["watchers"]) == 1


@pytest.mark.anyio("asyncio")
async def test_get_ticket_not_found(monkeypatch):
    _patch_module(monkeypatch, _module())

    async def fake_get_ticket(ticket_id):
        return None

    monkeypatch.setattr(ollama_service.tickets_repo, "get_ticket", fake_get_ticket)

    with pytest.raises(ollama_service.OllamaMCPError) as exc:
        await ollama_service.handle_rpc_request(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "get_ticket", "arguments": {"ticket_id": 999}},
            },
            "Bearer secret",
        )
    assert exc.value.status_code == 404


@pytest.mark.anyio("asyncio")
async def test_list_ticket_statuses(monkeypatch):
    _patch_module(
        monkeypatch,
        _module(settings={"allowed_statuses": ["open", "closed"]}),
    )

    response = await ollama_service.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "list_ticket_statuses", "arguments": {}},
        },
        "Bearer secret",
    )

    assert response["result"]["structuredContent"] == {
        "statuses": ["open", "closed"]
    }


@pytest.mark.anyio("asyncio")
async def test_create_reply_requires_allow_flag(monkeypatch):
    _patch_module(
        monkeypatch,
        _module(
            settings={
                "allowed_actions": ["create_ticket_reply"],
                "allow_ticket_replies": False,
            }
        ),
    )

    with pytest.raises(ollama_service.OllamaMCPError) as exc:
        await ollama_service.handle_rpc_request(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "create_ticket_reply",
                    "arguments": {"ticket_id": 1, "body": "hi"},
                },
            },
            "Bearer secret",
        )
    assert exc.value.status_code == 403
    assert exc.value.rpc_code == ollama_service.ERR_FORBIDDEN


@pytest.mark.anyio("asyncio")
async def test_update_ticket_requires_allow_flag(monkeypatch):
    _patch_module(
        monkeypatch,
        _module(
            settings={
                "allowed_actions": ["update_ticket"],
                "allow_ticket_updates": False,
            }
        ),
    )

    with pytest.raises(ollama_service.OllamaMCPError) as exc:
        await ollama_service.handle_rpc_request(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "update_ticket",
                    "arguments": {"ticket_id": 1, "status": "open"},
                },
            },
            "Bearer secret",
        )
    assert exc.value.status_code == 403


@pytest.mark.anyio("asyncio")
async def test_invalid_token(monkeypatch):
    _patch_module(monkeypatch, _module())

    with pytest.raises(ollama_service.OllamaMCPError) as exc:
        await ollama_service.handle_rpc_request(
            {"jsonrpc": "2.0", "id": 11, "method": "tools/list"},
            "Bearer wrong",
        )
    assert exc.value.status_code == 401
    assert exc.value.rpc_code == ollama_service.ERR_AUTH


@pytest.mark.anyio("asyncio")
async def test_missing_authorization_header_rejected(monkeypatch):
    _patch_module(monkeypatch, _module())

    with pytest.raises(ollama_service.OllamaMCPError) as exc:
        await ollama_service.handle_rpc_request(
            {"jsonrpc": "2.0", "id": 12, "method": "tools/list"},
            None,
        )
    assert exc.value.status_code == 401


@pytest.mark.anyio("asyncio")
async def test_disabled_module_rejects(monkeypatch):
    _patch_module(monkeypatch, _module(enabled=False))

    with pytest.raises(ollama_service.OllamaMCPError) as exc:
        await ollama_service.handle_rpc_request(
            {"jsonrpc": "2.0", "id": 13, "method": "tools/list"},
            "Bearer secret",
        )
    assert exc.value.status_code == 503
    assert exc.value.rpc_code == ollama_service.ERR_DISABLED


@pytest.mark.anyio("asyncio")
async def test_unknown_method(monkeypatch):
    _patch_module(monkeypatch, _module())

    with pytest.raises(ollama_service.OllamaMCPError) as exc:
        await ollama_service.handle_rpc_request(
            {"jsonrpc": "2.0", "id": 14, "method": "no_such_method"},
            "Bearer secret",
        )
    assert exc.value.rpc_code == ollama_service.ERR_METHOD_NOT_FOUND


def test_public_manifest_has_no_secrets():
    manifest = ollama_service.public_manifest()
    assert manifest["protocolVersion"] == ollama_service.PROTOCOL_VERSION
    assert manifest["authentication"]["scheme"] == "Bearer"
    assert "search_tickets" in manifest["tools"]
    # Ensure no secret-bearing field names slip into the manifest payload.
    forbidden = {"shared_secret", "shared_secret_hash", "secret_hash", "token"}
    assert not (forbidden & set(manifest.keys()))
    assert not (forbidden & set(manifest["authentication"].keys()))
