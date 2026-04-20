"""Ollama-compatible Model Context Protocol (MCP) service.

Implements the **standard** MCP JSON-RPC surface used by Ollama's MCP client
(and other generic MCP clients), which differs from the bespoke ChatGPT MCP
endpoint in two important ways:

* Method names follow the spec: ``initialize``, ``notifications/initialized``,
  ``ping``, ``tools/list``, ``tools/call``, ``shutdown``.
* Tool results are returned as ``{"content": [{"type": "text", "text": ...}],
  "structuredContent": {...}, "isError": false}``.

The endpoint is read-only by default and scoped to ticket queries — admins can
opt in to reply / update tools via module settings.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from fastapi import status
from loguru import logger

from app.repositories import integration_modules as module_repo
from app.repositories import tickets as tickets_repo
from app.services import tickets as tickets_service

from ._common import (
    constant_time_compare,
    filter_sensitive_fields,
    hash_secret,
    serialise_reply,
    serialise_ticket,
    serialise_watcher,
)


MODULE_SLUG = "ollama-mcp"

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_SERVER_NAME = "MyPortal Ollama MCP"
DEFAULT_SERVER_VERSION = "1.0.0"

DEFAULT_TOOLS: tuple[str, ...] = (
    "search_tickets",
    "list_tickets",
    "get_ticket",
    "list_ticket_statuses",
)

OPTIONAL_WRITE_TOOLS: tuple[str, ...] = (
    "create_ticket_reply",
    "update_ticket",
)

ALL_TOOLS: tuple[str, ...] = DEFAULT_TOOLS + OPTIONAL_WRITE_TOOLS

DEFAULT_STATUSES: tuple[str, ...] = (
    "open",
    "pending",
    "in_progress",
    "resolved",
    "closed",
)

# JSON-RPC error codes (per the spec)
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
# Application-defined error codes (range -32000 to -32099 reserved for the server)
ERR_AUTH = -32001
ERR_FORBIDDEN = -32002
ERR_NOT_FOUND = -32004
ERR_DISABLED = -32010


class OllamaMCPError(Exception):
    """Raised when an Ollama MCP request cannot be fulfilled.

    Carries both an HTTP status (used by the route layer when the error happens
    *before* a JSON-RPC envelope can be built — e.g. authentication) and a
    JSON-RPC error code (used inside JSON-RPC error responses).
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        rpc_code: int = ERR_INTERNAL,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.rpc_code = rpc_code


# ---------------------------------------------------------------------------
# Settings & token resolution
# ---------------------------------------------------------------------------


def _resolve_token(authorization_header: str | None) -> str:
    """Extract a bearer token from the ``Authorization`` header.

    Ollama's MCP client always sends credentials via the ``Authorization``
    header, so we deliberately do not accept tokens from the JSON-RPC body.
    """

    if not authorization_header:
        raise OllamaMCPError(
            status.HTTP_401_UNAUTHORIZED,
            "Authorization header is required",
            rpc_code=ERR_AUTH,
        )
    parts = authorization_header.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise OllamaMCPError(
            status.HTTP_401_UNAUTHORIZED,
            "Authorization header must be 'Bearer <token>'",
            rpc_code=ERR_AUTH,
        )
    return parts[1]


async def _load_settings() -> dict[str, Any]:
    module = await module_repo.get_module(MODULE_SLUG)
    if not module or not module.get("enabled"):
        raise OllamaMCPError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Ollama MCP module is disabled",
            rpc_code=ERR_DISABLED,
        )
    settings_obj = module.get("settings") or {}
    if not isinstance(settings_obj, Mapping):
        raise OllamaMCPError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Ollama MCP settings are invalid",
            rpc_code=ERR_DISABLED,
        )
    secret_hash = str(settings_obj.get("shared_secret_hash") or "").strip()
    if not secret_hash:
        raise OllamaMCPError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Ollama MCP shared secret is not configured",
            rpc_code=ERR_DISABLED,
        )
    raw_actions = settings_obj.get("allowed_actions") or list(DEFAULT_TOOLS)
    if isinstance(raw_actions, str):
        raw_actions = [raw_actions]
    allowed_actions = [name for name in raw_actions if name in ALL_TOOLS]
    if not allowed_actions:
        allowed_actions = list(DEFAULT_TOOLS)
    raw_max = settings_obj.get("max_results")
    try:
        max_results = int(raw_max) if raw_max is not None else 25
    except (TypeError, ValueError):
        max_results = 25
    max_results = max(1, min(max_results, 200))
    raw_statuses = settings_obj.get("allowed_statuses") or list(DEFAULT_STATUSES)
    if isinstance(raw_statuses, str):
        raw_statuses = [raw_statuses]
    allowed_statuses = [str(s).strip().lower() for s in raw_statuses if str(s).strip()]
    return {
        "secret_hash": secret_hash,
        "allowed_actions": allowed_actions,
        "max_results": max_results,
        "allow_ticket_replies": bool(settings_obj.get("allow_ticket_replies")),
        "allow_ticket_updates": bool(settings_obj.get("allow_ticket_updates")),
        "allowed_statuses": allowed_statuses or list(DEFAULT_STATUSES),
        "system_user_id": settings_obj.get("system_user_id"),
        "include_internal_replies": bool(settings_obj.get("include_internal_replies")),
        "server_name": str(settings_obj.get("server_name") or DEFAULT_SERVER_NAME),
        "server_version": str(settings_obj.get("server_version") or DEFAULT_SERVER_VERSION),
    }


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


def _tool_definitions(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    max_results = int(settings.get("max_results", 25))
    definitions: dict[str, dict[str, Any]] = {
        "search_tickets": {
            "name": "search_tickets",
            "description": (
                "Search MyPortal tickets by free-text query. Matches the ticket "
                "subject, description and external reference. Optional filters "
                "narrow the search by status, company, assignee, or module."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Free-text search term. Required.",
                    },
                    "status": {"type": "string"},
                    "company_id": {"type": "integer"},
                    "assigned_user_id": {"type": "integer"},
                    "module_slug": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": max_results,
                    },
                },
            },
        },
        "list_tickets": {
            "name": "list_tickets",
            "description": (
                "List recent MyPortal tickets, optionally filtered by status, "
                "company, assignee, module, or a free-text search term."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "company_id": {"type": "integer"},
                    "assigned_user_id": {"type": "integer"},
                    "module_slug": {"type": "string"},
                    "search": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": max_results,
                    },
                },
            },
        },
        "get_ticket": {
            "name": "get_ticket",
            "description": (
                "Retrieve full details for a single ticket, including replies "
                "and watchers."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["ticket_id"],
                "properties": {"ticket_id": {"type": "integer"}},
            },
        },
        "list_ticket_statuses": {
            "name": "list_ticket_statuses",
            "description": "Return the list of ticket statuses configured for this MCP server.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        "create_ticket_reply": {
            "name": "create_ticket_reply",
            "description": (
                "Append a reply to an existing ticket. Disabled unless the "
                "module's 'allow_ticket_replies' setting is enabled."
            ),
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
        "update_ticket": {
            "name": "update_ticket",
            "description": (
                "Update a ticket's status, priority, assignment, category, or "
                "module. Disabled unless the module's 'allow_ticket_updates' "
                "setting is enabled."
            ),
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
    allowed = settings.get("allowed_actions") or list(DEFAULT_TOOLS)
    return [definitions[name] for name in allowed if name in definitions]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _coerce_optional_int(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise OllamaMCPError(
            status.HTTP_400_BAD_REQUEST,
            f"{field} must be an integer",
            rpc_code=ERR_INVALID_PARAMS,
        )


def _resolve_limit(arguments: Mapping[str, Any], settings: Mapping[str, Any]) -> int:
    max_results = int(settings.get("max_results", 25))
    raw = arguments.get("limit")
    if raw is None:
        return max_results
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise OllamaMCPError(
            status.HTTP_400_BAD_REQUEST,
            "limit must be an integer",
            rpc_code=ERR_INVALID_PARAMS,
        )
    return max(1, min(value, max_results))


async def _list_tickets(
    arguments: Mapping[str, Any],
    settings: Mapping[str, Any],
    *,
    require_query: bool = False,
) -> dict[str, Any]:
    query = arguments.get("query") if require_query else arguments.get("search")
    if require_query:
        if not query or not str(query).strip():
            raise OllamaMCPError(
                status.HTTP_400_BAD_REQUEST,
                "query is required for search_tickets",
                rpc_code=ERR_INVALID_PARAMS,
            )
    search_term = str(query).strip() if query else None

    limit_value = _resolve_limit(arguments, settings)
    status_filter = arguments.get("status")
    module_slug = arguments.get("module_slug") or arguments.get("moduleSlug")
    company_id = _coerce_optional_int(
        arguments.get("company_id") or arguments.get("companyId"), "company_id"
    )
    assigned_user_id = _coerce_optional_int(
        arguments.get("assigned_user_id") or arguments.get("assignedUserId"),
        "assigned_user_id",
    )

    tickets = await tickets_repo.list_tickets(
        status=str(status_filter).strip() or None if status_filter else None,
        module_slug=str(module_slug).strip() or None if module_slug else None,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        search=search_term,
        limit=limit_value,
    )
    serialised = [filter_sensitive_fields(serialise_ticket(t)) for t in tickets]
    payload: dict[str, Any] = {
        "tickets": serialised,
        "count": len(serialised),
        "limit": limit_value,
    }
    if search_term is not None:
        payload["query"] = search_term
    return payload


async def _get_ticket(
    arguments: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    ticket_id = _coerce_optional_int(arguments.get("ticket_id"), "ticket_id")
    if ticket_id is None:
        raise OllamaMCPError(
            status.HTTP_400_BAD_REQUEST,
            "ticket_id is required",
            rpc_code=ERR_INVALID_PARAMS,
        )
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise OllamaMCPError(
            status.HTTP_404_NOT_FOUND,
            f"Ticket {ticket_id} not found",
            rpc_code=ERR_NOT_FOUND,
        )
    include_internal = bool(settings.get("include_internal_replies"))
    replies = await tickets_repo.list_replies(
        ticket_id, include_internal=include_internal
    )
    watchers = await tickets_repo.list_watchers(ticket_id)
    return {
        "ticket": filter_sensitive_fields(serialise_ticket(ticket)),
        "replies": [filter_sensitive_fields(serialise_reply(r)) for r in replies],
        "watchers": [filter_sensitive_fields(serialise_watcher(w)) for w in watchers],
    }


def _list_statuses(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {"statuses": list(settings.get("allowed_statuses") or DEFAULT_STATUSES)}


async def _create_reply(
    arguments: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    if not settings.get("allow_ticket_replies"):
        raise OllamaMCPError(
            status.HTTP_403_FORBIDDEN,
            "Ticket replies are disabled for the Ollama MCP server",
            rpc_code=ERR_FORBIDDEN,
        )
    ticket_id = _coerce_optional_int(arguments.get("ticket_id"), "ticket_id")
    if ticket_id is None:
        raise OllamaMCPError(
            status.HTTP_400_BAD_REQUEST,
            "ticket_id is required",
            rpc_code=ERR_INVALID_PARAMS,
        )
    body = str(arguments.get("body") or "").strip()
    if not body:
        raise OllamaMCPError(
            status.HTTP_400_BAD_REQUEST,
            "Reply body cannot be empty",
            rpc_code=ERR_INVALID_PARAMS,
        )
    author_override = arguments.get("author_id")
    author_id: int | None = None
    if author_override not in (None, ""):
        author_id = _coerce_optional_int(author_override, "author_id")
    elif settings.get("system_user_id") not in (None, ""):
        try:
            author_id = int(settings["system_user_id"])
        except (TypeError, ValueError):
            author_id = None
    if author_id is None:
        raise OllamaMCPError(
            status.HTTP_400_BAD_REQUEST,
            "A system_user_id must be configured or author_id provided",
            rpc_code=ERR_INVALID_PARAMS,
        )
    is_internal = bool(arguments.get("is_internal"))
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise OllamaMCPError(
            status.HTTP_404_NOT_FOUND,
            f"Ticket {ticket_id} not found",
            rpc_code=ERR_NOT_FOUND,
        )
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
        "ticket": filter_sensitive_fields(serialise_ticket(ticket)),
        "reply": filter_sensitive_fields(serialise_reply(reply)),
    }


async def _update_ticket(
    arguments: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    if not settings.get("allow_ticket_updates"):
        raise OllamaMCPError(
            status.HTTP_403_FORBIDDEN,
            "Ticket updates are disabled for the Ollama MCP server",
            rpc_code=ERR_FORBIDDEN,
        )
    ticket_id = _coerce_optional_int(arguments.get("ticket_id"), "ticket_id")
    if ticket_id is None:
        raise OllamaMCPError(
            status.HTTP_400_BAD_REQUEST,
            "ticket_id is required",
            rpc_code=ERR_INVALID_PARAMS,
        )
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise OllamaMCPError(
            status.HTTP_404_NOT_FOUND,
            f"Ticket {ticket_id} not found",
            rpc_code=ERR_NOT_FOUND,
        )
    updates: dict[str, Any] = {}
    if arguments.get("status") not in (None, ""):
        status_value = str(arguments["status"]).strip().lower()
        allowed_statuses = {
            str(s).lower() for s in settings.get("allowed_statuses", [])
        }
        if allowed_statuses and status_value not in allowed_statuses:
            raise OllamaMCPError(
                status.HTTP_400_BAD_REQUEST,
                f"Status '{status_value}' is not permitted",
                rpc_code=ERR_INVALID_PARAMS,
            )
        updates["status"] = status_value
    if arguments.get("priority") not in (None, ""):
        updates["priority"] = str(arguments["priority"]).strip().lower()
    if arguments.get("assigned_user_id") not in (None, ""):
        updates["assigned_user_id"] = _coerce_optional_int(
            arguments["assigned_user_id"], "assigned_user_id"
        )
    if arguments.get("category") not in (None, ""):
        updates["category"] = str(arguments["category"]).strip()
    if arguments.get("module_slug") not in (None, ""):
        updates["module_slug"] = str(arguments["module_slug"]).strip()
    if not updates:
        raise OllamaMCPError(
            status.HTTP_400_BAD_REQUEST,
            "Provide at least one field to update",
            rpc_code=ERR_INVALID_PARAMS,
        )
    await tickets_repo.update_ticket(ticket_id, **updates)
    updated_ticket = await tickets_repo.get_ticket(ticket_id)
    await tickets_service.emit_ticket_updated_event(
        updated_ticket or ticket,
        actor_type="automation",
    )
    return {
        "ticket": filter_sensitive_fields(serialise_ticket(updated_ticket or ticket)),
        "updated_fields": updates,
    }


async def _execute_tool(
    name: str,
    arguments: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    allowed = settings.get("allowed_actions") or list(DEFAULT_TOOLS)
    if name not in allowed:
        raise OllamaMCPError(
            status.HTTP_403_FORBIDDEN,
            f"Tool '{name}' is not enabled for this MCP server",
            rpc_code=ERR_FORBIDDEN,
        )
    if name == "search_tickets":
        return await _list_tickets(arguments, settings, require_query=True)
    if name == "list_tickets":
        return await _list_tickets(arguments, settings, require_query=False)
    if name == "get_ticket":
        return await _get_ticket(arguments, settings)
    if name == "list_ticket_statuses":
        return _list_statuses(settings)
    if name == "create_ticket_reply":
        return await _create_reply(arguments, settings)
    if name == "update_ticket":
        return await _update_ticket(arguments, settings)
    raise OllamaMCPError(
        status.HTTP_400_BAD_REQUEST,
        f"Unsupported tool: {name}",
        rpc_code=ERR_INVALID_PARAMS,
    )


# ---------------------------------------------------------------------------
# JSON-RPC envelope handling
# ---------------------------------------------------------------------------


def _is_notification(method: str, request_id: Any) -> bool:
    """JSON-RPC notifications have no ``id`` field and never expect a response."""

    return request_id is None and method.startswith("notifications/")


def _ok_envelope(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_envelope(
    request_id: Any, code: int, message: str, *, data: Any = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _tool_call_result(data: dict[str, Any]) -> dict[str, Any]:
    """Build a spec-compliant ``tools/call`` result payload.

    Per the MCP spec, results carry an array of content blocks. We emit a single
    ``text`` block containing pretty-printed JSON (which Ollama can read), plus
    ``structuredContent`` for clients that prefer parsed data.
    """

    text = json.dumps(data, indent=2, sort_keys=True, default=str)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": data,
        "isError": False,
    }


async def handle_rpc_request(
    body: Mapping[str, Any] | None,
    authorization_header: str | None,
) -> dict[str, Any] | None:
    """Process a single JSON-RPC request and return the response envelope.

    Returns ``None`` for JSON-RPC notifications (which must not produce a
    response per the spec).
    """

    if not isinstance(body, Mapping):
        raise OllamaMCPError(
            status.HTTP_400_BAD_REQUEST,
            "Request body must be a JSON object",
            rpc_code=ERR_INVALID_REQUEST,
        )

    method = str(body.get("method") or "").strip()
    request_id = body.get("id")

    if not method:
        raise OllamaMCPError(
            status.HTTP_400_BAD_REQUEST,
            "JSON-RPC method is required",
            rpc_code=ERR_INVALID_REQUEST,
        )

    # Notifications never return a body — but we still authenticate to avoid
    # leaking the fact that the endpoint is responsive to unauthenticated peers.
    is_notification = _is_notification(method, request_id)

    token = _resolve_token(authorization_header)
    settings_obj = await _load_settings()
    if not constant_time_compare(hash_secret(token), settings_obj["secret_hash"]):
        raise OllamaMCPError(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid Ollama MCP token",
            rpc_code=ERR_AUTH,
        )

    try:
        if method == "initialize":
            params = body.get("params") if isinstance(body.get("params"), Mapping) else {}
            client_protocol = str(params.get("protocolVersion") or PROTOCOL_VERSION)
            result = {
                "protocolVersion": client_protocol or PROTOCOL_VERSION,
                "serverInfo": {
                    "name": settings_obj["server_name"],
                    "version": settings_obj["server_version"],
                },
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "instructions": (
                    "Use search_tickets to find MyPortal tickets matching a "
                    "free-text query, list_tickets to browse recent tickets, "
                    "and get_ticket for full ticket detail."
                ),
            }
            return _ok_envelope(request_id, result)

        if method in {"notifications/initialized", "notifications/cancelled"}:
            # JSON-RPC notification — no response.
            return None

        if method == "ping":
            return _ok_envelope(request_id, {})

        if method == "shutdown":
            return _ok_envelope(request_id, {})

        if method == "tools/list":
            return _ok_envelope(
                request_id, {"tools": _tool_definitions(settings_obj)}
            )

        if method == "tools/call":
            params = body.get("params")
            if not isinstance(params, Mapping):
                raise OllamaMCPError(
                    status.HTTP_400_BAD_REQUEST,
                    "params must be an object",
                    rpc_code=ERR_INVALID_PARAMS,
                )
            name = str(params.get("name") or "").strip()
            if not name:
                raise OllamaMCPError(
                    status.HTTP_400_BAD_REQUEST,
                    "Tool name is required",
                    rpc_code=ERR_INVALID_PARAMS,
                )
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, Mapping):
                raise OllamaMCPError(
                    status.HTTP_400_BAD_REQUEST,
                    "Tool arguments must be an object",
                    rpc_code=ERR_INVALID_PARAMS,
                )
            data = await _execute_tool(name, arguments, settings_obj)
            return _ok_envelope(request_id, _tool_call_result(data))

        # Unknown method — return a notification-safe response.
        if is_notification:
            return None
        raise OllamaMCPError(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported MCP method: {method}",
            rpc_code=ERR_METHOD_NOT_FOUND,
        )
    except OllamaMCPError:
        raise
    except Exception:
        logger.exception("Unexpected error processing Ollama MCP request")
        raise OllamaMCPError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Internal server error",
            rpc_code=ERR_INTERNAL,
        )


def build_error_response(request_id: Any, exc: OllamaMCPError) -> dict[str, Any]:
    """Convert an :class:`OllamaMCPError` into a JSON-RPC error envelope."""

    return _error_envelope(request_id, exc.rpc_code, exc.message)


def public_manifest() -> dict[str, Any]:
    """Return a non-secret discovery manifest for the Ollama MCP server."""

    return {
        "name": DEFAULT_SERVER_NAME,
        "version": DEFAULT_SERVER_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "transport": "http+json-rpc",
        "endpoint": "/api/mcp/ollama/",
        "authentication": {
            "scheme": "Bearer",
            "header": "Authorization",
            "description": (
                "Provide the shared secret configured for the 'ollama-mcp' "
                "module as a bearer token."
            ),
        },
        "tools": list(DEFAULT_TOOLS),
        "optional_tools": list(OPTIONAL_WRITE_TOOLS),
    }
