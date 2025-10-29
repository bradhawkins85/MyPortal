from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import status

from app.repositories import integration_modules as module_repo
from app.repositories import tickets as tickets_repo
from app.services import tickets as tickets_service

DEFAULT_TOOLS = [
    "listTickets",
    "getTicket",
    "createTicketReply",
    "updateTicket",
]


class ChatGPTMCPError(Exception):
    """Raised when ChatGPT MCP requests cannot be fulfilled."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _constant_time_compare(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(candidate, expected)


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _coerce_optional_int(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, f"{field} must be an integer")


def _serialise_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    if value is None:
        return None
    return str(value)


def _serialise_ticket(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "subject": record.get("subject"),
        "description": record.get("description"),
        "status": record.get("status"),
        "priority": record.get("priority"),
        "category": record.get("category"),
        "module_slug": record.get("module_slug"),
        "company_id": record.get("company_id"),
        "requester_id": record.get("requester_id"),
        "assigned_user_id": record.get("assigned_user_id"),
        "external_reference": record.get("external_reference"),
        "created_at": _serialise_datetime(record.get("created_at")),
        "updated_at": _serialise_datetime(record.get("updated_at")),
        "closed_at": _serialise_datetime(record.get("closed_at")),
    }


def _serialise_reply(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "ticket_id": record.get("ticket_id"),
        "author_id": record.get("author_id"),
        "body": record.get("body"),
        "is_internal": bool(record.get("is_internal")),
        "created_at": _serialise_datetime(record.get("created_at")),
    }


def _serialise_watcher(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "ticket_id": record.get("ticket_id"),
        "user_id": record.get("user_id"),
        "created_at": _serialise_datetime(record.get("created_at")),
    }


def _resolve_token(body: Mapping[str, Any], authorization_header: str | None) -> str:
    if authorization_header:
        parts = authorization_header.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1]:
            return parts[1]
    for key in ("accessToken", "token", "apiKey"):
        candidate = body.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ChatGPTMCPError(status.HTTP_401_UNAUTHORIZED, "ChatGPT MCP token is required")


async def _load_settings() -> dict[str, Any]:
    module = await module_repo.get_module("chatgpt-mcp")
    if not module or not module.get("enabled"):
        raise ChatGPTMCPError(status.HTTP_503_SERVICE_UNAVAILABLE, "ChatGPT MCP module is disabled")
    settings = module.get("settings") or {}
    if not isinstance(settings, Mapping):
        raise ChatGPTMCPError(status.HTTP_503_SERVICE_UNAVAILABLE, "ChatGPT MCP settings are invalid")
    secret_hash = str(settings.get("shared_secret_hash") or "").strip()
    if not secret_hash:
        raise ChatGPTMCPError(status.HTTP_503_SERVICE_UNAVAILABLE, "ChatGPT MCP shared secret is not configured")
    allowed_actions = settings.get("allowed_actions") or list(DEFAULT_TOOLS)
    max_results = settings.get("max_results") or 50
    allow_updates = bool(settings.get("allow_ticket_updates"))
    allowed_statuses = settings.get("allowed_statuses") or [
        "open",
        "pending",
        "in_progress",
        "resolved",
        "closed",
    ]
    system_user_id = settings.get("system_user_id")
    return {
        "secret_hash": secret_hash,
        "allowed_actions": allowed_actions,
        "max_results": max_results,
        "allow_ticket_updates": allow_updates,
        "allowed_statuses": allowed_statuses,
        "system_user_id": system_user_id,
    }


def _build_tools(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {
        "listTickets": {
            "name": "listTickets",
            "description": "Return recent tickets filtered by status, company, assignee, or module.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "company_id": {"type": "integer"},
                    "assigned_user_id": {"type": "integer"},
                    "module_slug": {"type": "string"},
                    "search": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
        },
        "getTicket": {
            "name": "getTicket",
            "description": "Retrieve a single ticket with replies and watchers.",
            "inputSchema": {
                "type": "object",
                "required": ["ticket_id"],
                "properties": {"ticket_id": {"type": "integer"}},
            },
        },
        "createTicketReply": {
            "name": "createTicketReply",
            "description": "Append a reply to an existing ticket using the configured system user.",
            "inputSchema": {
                "type": "object",
                "required": ["ticket_id", "body"],
                "properties": {
                    "ticket_id": {"type": "integer"},
                    "body": {"type": "string"},
                    "is_internal": {"type": "boolean"},
                    "author_id": {"type": "integer"},
                },
            },
        },
        "updateTicket": {
            "name": "updateTicket",
            "description": "Update ticket status, priority, assignment, or metadata when permitted.",
            "inputSchema": {
                "type": "object",
                "required": ["ticket_id"],
                "properties": {
                    "ticket_id": {"type": "integer"},
                    "status": {"type": "string"},
                    "priority": {"type": "string"},
                    "assigned_user_id": {"type": "integer"},
                    "category": {"type": "string"},
                    "module_slug": {"type": "string"},
                },
            },
        },
    }
    allowed = settings.get("allowed_actions") or DEFAULT_TOOLS
    return [definitions[name] for name in allowed if name in definitions]


async def _list_tickets(arguments: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    limit = arguments.get("limit")
    try:
        limit_value = int(limit) if limit is not None else int(settings.get("max_results", 50))
    except (TypeError, ValueError):
        limit_value = int(settings.get("max_results", 50))
    limit_value = max(1, min(limit_value, int(settings.get("max_results", 50))))
    status_filter = arguments.get("status")
    module_slug = arguments.get("module_slug") or arguments.get("moduleSlug")
    company_id = _coerce_optional_int(arguments.get("company_id") or arguments.get("companyId"), "company_id")
    assigned_user_id = _coerce_optional_int(
        arguments.get("assigned_user_id") or arguments.get("assignedUserId"),
        "assigned_user_id",
    )
    search = arguments.get("search")

    tickets = await tickets_repo.list_tickets(
        status=str(status_filter).strip() or None if status_filter else None,
        module_slug=str(module_slug).strip() or None if module_slug else None,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        search=str(search).strip() or None if search else None,
        limit=limit_value,
    )
    return {"tickets": [_serialise_ticket(ticket) for ticket in tickets], "limit": limit_value}


async def _get_ticket(arguments: Mapping[str, Any]) -> dict[str, Any]:
    try:
        ticket_id = int(arguments.get("ticket_id"))
    except (TypeError, ValueError):
        raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, "ticket_id must be an integer")
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise ChatGPTMCPError(status.HTTP_404_NOT_FOUND, f"Ticket {ticket_id} not found")
    replies = await tickets_repo.list_replies(ticket_id, include_internal=True)
    watchers = await tickets_repo.list_watchers(ticket_id)
    return {
        "ticket": _serialise_ticket(ticket),
        "replies": [_serialise_reply(reply) for reply in replies],
        "watchers": [_serialise_watcher(watcher) for watcher in watchers],
    }


async def _create_reply(arguments: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    try:
        ticket_id = int(arguments.get("ticket_id"))
    except (TypeError, ValueError):
        raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, "ticket_id must be an integer")
    body = str(arguments.get("body") or "").strip()
    if not body:
        raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, "Reply body cannot be empty")
    author_override = arguments.get("author_id")
    author_id = None
    if author_override not in (None, ""):
        try:
            author_id = int(author_override)
        except (TypeError, ValueError):
            raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, "author_id must be an integer")
    elif settings.get("system_user_id") not in (None, ""):
        author_id = int(settings["system_user_id"])
    else:
        raise ChatGPTMCPError(
            status.HTTP_400_BAD_REQUEST,
            "A system_user_id must be configured for automated replies or author_id provided",
        )
    is_internal = bool(arguments.get("is_internal"))
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise ChatGPTMCPError(status.HTTP_404_NOT_FOUND, f"Ticket {ticket_id} not found")
    reply = await tickets_repo.create_reply(
        ticket_id=ticket_id,
        author_id=author_id,
        body=body,
        is_internal=is_internal,
    )
    actor_payload = {"id": author_id} if author_id is not None else None
    await tickets_service.emit_ticket_updated_event(
        ticket,
        actor_type="automation",
        actor=actor_payload,
    )
    return {
        "ticket": _serialise_ticket(ticket),
        "reply": _serialise_reply(reply),
    }


async def _update_ticket(arguments: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    if not settings.get("allow_ticket_updates"):
        raise ChatGPTMCPError(status.HTTP_403_FORBIDDEN, "Ticket updates are disabled for ChatGPT")
    try:
        ticket_id = int(arguments.get("ticket_id"))
    except (TypeError, ValueError):
        raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, "ticket_id must be an integer")
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise ChatGPTMCPError(status.HTTP_404_NOT_FOUND, f"Ticket {ticket_id} not found")
    updates: dict[str, Any] = {}
    if "status" in arguments and arguments["status"] not in (None, ""):
        status_value = str(arguments["status"]).strip().lower()
        allowed_statuses = {status.lower() for status in settings.get("allowed_statuses", [])}
        if allowed_statuses and status_value not in allowed_statuses:
            raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, "Status is not permitted for ChatGPT updates")
        updates["status"] = status_value
    if "priority" in arguments and arguments["priority"] not in (None, ""):
        updates["priority"] = str(arguments["priority"]).strip().lower()
    if "assigned_user_id" in arguments and arguments["assigned_user_id"] not in (None, ""):
        try:
            updates["assigned_user_id"] = int(arguments["assigned_user_id"])
        except (TypeError, ValueError):
            raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, "assigned_user_id must be an integer")
    if "category" in arguments and arguments["category"] not in (None, ""):
        updates["category"] = str(arguments["category"]).strip()
    if "module_slug" in arguments and arguments["module_slug"] not in (None, ""):
        updates["module_slug"] = str(arguments["module_slug"]).strip()
    if not updates:
        raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, "Provide at least one field to update")
    await tickets_repo.update_ticket(ticket_id, **updates)
    updated_ticket = await tickets_repo.get_ticket(ticket_id)
    await tickets_service.emit_ticket_updated_event(
        updated_ticket or ticket,
        actor_type="automation",
    )
    return {"ticket": _serialise_ticket(updated_ticket or ticket), "updated_fields": updates}


async def _execute_tool(name: str, arguments: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    allowed = settings.get("allowed_actions") or DEFAULT_TOOLS
    if name not in allowed:
        raise ChatGPTMCPError(status.HTTP_403_FORBIDDEN, f"Tool {name} is not enabled")
    if name == "listTickets":
        return await _list_tickets(arguments, settings)
    if name == "getTicket":
        return await _get_ticket(arguments)
    if name == "createTicketReply":
        return await _create_reply(arguments, settings)
    if name == "updateTicket":
        return await _update_ticket(arguments, settings)
    raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, f"Unsupported tool {name}")


async def handle_rpc_request(body: Mapping[str, Any], authorization_header: str | None) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, "Request body must be a JSON object")
    token = _resolve_token(body, authorization_header)
    settings = await _load_settings()
    provided_hash = _hash_secret(token)
    if not _constant_time_compare(provided_hash, settings["secret_hash"]):
        raise ChatGPTMCPError(status.HTTP_401_UNAUTHORIZED, "Invalid ChatGPT MCP token")

    method = str(body.get("method") or "").strip()
    if not method:
        raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, "MCP method is required")
    request_id = body.get("id")

    if method in {"ping"}:
        result = {"ok": True}
    elif method in {"initialize"}:
        result = {
            "name": "MyPortal Tickets MCP",
            "version": "1.0.0",
            "capabilities": {"tools": True, "resources": False},
        }
    elif method in {"listTools", "tools.list"}:
        result = {"tools": _build_tools(settings)}
    elif method in {"callTool", "tools.call"}:
        params = body.get("params")
        if not isinstance(params, Mapping):
            raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, "params must be an object")
        name = str(params.get("name") or params.get("tool") or "").strip()
        if not name:
            raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, "Tool name is required")
        arguments = params.get("arguments") or params.get("args") or {}
        if not isinstance(arguments, Mapping):
            raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, "Tool arguments must be an object")
        data = await _execute_tool(name, arguments, settings)
        result = {"content": [{"type": "json", "data": data}]}
    else:
        raise ChatGPTMCPError(status.HTTP_400_BAD_REQUEST, f"Unsupported MCP method {method}")

    return {
        "jsonrpc": body.get("jsonrpc", "2.0"),
        "id": request_id,
        "result": result,
    }
