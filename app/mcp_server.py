"""Model Context Protocol (MCP) WebSocket server for authorized agent access.

This module provides a secure, read-only WebSocket endpoint for authorized agents
(e.g., GitHub Copilot) to interact with live application data. All operations are
token-authenticated and rate-limited.

Security Features:
- Disabled by default (requires MCP_ENABLED=true)
- Token authentication required (via header or query param)
- Read-only operations only (no DB writes)
- Whitelist of allowed models
- Sensitive fields automatically filtered
- Per-connection rate limiting
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect, status
from loguru import logger

from app.core.config import get_settings
from app.core.database import db


settings = get_settings()


# Maximum length for SQL identifier names to prevent abuse
_MAX_IDENTIFIER_LENGTH = 128

# Pattern for valid SQL identifiers (alphanumeric and underscore, starting with letter or underscore)
_VALID_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _is_valid_identifier(name: str) -> bool:
    """Validate that a string is a safe SQL identifier.
    
    Only allows alphanumeric characters and underscores, and must start with
    a letter or underscore. This prevents SQL injection through field names.
    
    Args:
        name: The identifier to validate
        
    Returns:
        True if the identifier is safe to use in SQL queries
    """
    if not name or len(name) > _MAX_IDENTIFIER_LENGTH:
        return False
    return _VALID_IDENTIFIER_PATTERN.match(name) is not None


# Define sensitive fields to exclude from all responses
SENSITIVE_FIELDS = {
    "password_hash",
    "password",
    "secret",
    "token",
    "api_key",
    "totp_secret",
    "encryption_key",
    "private_key",
    "client_secret",
    "webhook_secret",
    "auth_token",
}


_MCP_MODEL_SELECTS: dict[str, str] = {
    "users": "SELECT * FROM users",
    "tickets": "SELECT * FROM tickets",
    "change_log": "SELECT * FROM change_log",
}

_MCP_MODEL_GET_BY_ID: dict[str, str] = {
    model: f"{select_sql} WHERE id = %s"
    for model, select_sql in _MCP_MODEL_SELECTS.items()
}

_MCP_ALLOWED_FILTERS: dict[str, dict[str, str]] = {
    "users": {
        "id": "id",
        "email": "email",
        "company_id": "company_id",
        "status": "status",
        "role_id": "role_id",
        "is_active": "is_active",
    },
    "tickets": {
        "id": "id",
        "company_id": "company_id",
        "status": "status",
        "priority": "priority",
        "assigned_to": "assigned_to",
        "requester_email": "requester_email",
        "ticket_number": "ticket_number",
    },
    "change_log": {
        "id": "id",
        "guid": "guid",
        "change_type": "change_type",
        "occurred_at": "occurred_at",
    },
}


def _get_allowed_models() -> set[str]:
    """Parse the configured list and intersect it with the supported MCP models."""
    if not settings.mcp_allowed_models:
        return set(_MCP_MODEL_SELECTS)

    requested_models = {
        model.strip().lower()
        for model in settings.mcp_allowed_models.split(",")
        if model.strip()
    }
    unsupported_models = requested_models - set(_MCP_MODEL_SELECTS)
    if unsupported_models:
        logger.warning(
            "Ignoring unsupported MCP models from configuration: {}",
            ", ".join(sorted(unsupported_models)),
        )
    return requested_models & set(_MCP_MODEL_SELECTS)


def _filter_sensitive_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive fields from a record."""
    return {k: v for k, v in record.items() if k.lower() not in SENSITIVE_FIELDS}


def _filter_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter sensitive fields from a list of records."""
    return [_filter_sensitive_fields(record) for record in records]


def _get_model_select_sql(model: str) -> str:
    """Return the static SELECT query for a supported MCP model."""
    try:
        return _MCP_MODEL_SELECTS[model]
    except KeyError as exc:
        raise ValueError(f"Unsupported model: {model}") from exc


def _get_model_get_sql(model: str) -> str:
    """Return the static SELECT-by-id query for a supported MCP model."""
    try:
        return _MCP_MODEL_GET_BY_ID[model]
    except KeyError as exc:
        raise ValueError(f"Unsupported model: {model}") from exc


def _get_allowed_filter_columns(model: str) -> dict[str, str]:
    """Return the static allowlist of filterable columns for a model."""
    try:
        return _MCP_ALLOWED_FILTERS[model]
    except KeyError as exc:
        raise ValueError(f"Unsupported model: {model}") from exc


class RateLimiter:
    """Simple sliding window rate limiter for WebSocket connections."""

    def __init__(self, max_requests: int, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: deque[float] = deque()

    def check_rate_limit(self) -> tuple[bool, str | None]:
        """Check if the rate limit is exceeded.
        
        Returns:
            Tuple of (allowed, error_message)
        """
        now = time.time()
        cutoff = now - self.window_seconds

        # Remove old requests outside the window
        while self.requests and self.requests[0] < cutoff:
            self.requests.popleft()

        if len(self.requests) >= self.max_requests:
            return False, f"Rate limit exceeded: {self.max_requests} requests per {self.window_seconds}s"

        self.requests.append(now)
        return True, None


async def _validate_token(websocket: WebSocket) -> tuple[bool, str | None]:
    """Validate MCP authentication token.
    
    Checks for token in:
    1. X-MCP-Token header
    2. token query parameter
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not settings.mcp_enabled:
        return False, "MCP server is disabled"

    if not settings.mcp_token:
        return False, "MCP server is not configured (missing MCP_TOKEN)"

    # Check header first
    token = websocket.headers.get("x-mcp-token")
    
    # Fall back to query parameter
    if not token:
        token = websocket.query_params.get("token")

    if not token:
        return False, "Missing authentication token"

    if token != settings.mcp_token:
        return False, "Invalid authentication token"

    return True, None


async def _handle_list_action(
    model: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Handle list action for a model.
    
    Params:
        limit: Maximum number of records (default 50, max 100)
        offset: Offset for pagination (default 0)
        filters: Dict of field=value equality filters
    """
    try:
        limit = int(params.get("limit", 50))
        offset = int(params.get("offset", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Pagination parameters must be integers") from exc
    if limit < 0 or offset < 0:
        raise ValueError("Pagination parameters must be non-negative")
    limit = min(limit, 100)

    filters = params.get("filters", {})
    if filters is None:
        filters = {}
    if not isinstance(filters, dict):
        raise ValueError("Filters must be an object of field/value pairs")

    select_sql = _get_model_select_sql(model)
    allowed_filter_columns = _get_allowed_filter_columns(model)

    # Build SQL query
    where_clauses = []
    where_params = []
    
    for field, value in filters.items():
        # Validate field name to prevent SQL injection
        if not _is_valid_identifier(field):
            raise ValueError(f"Invalid filter field name: {field}")
        column_name = allowed_filter_columns.get(field)
        if column_name is None:
            raise ValueError(f"Filter field '{field}' is not allowed for model '{model}'")
        # Simple equality filter only for security
        where_clauses.append(f"{column_name} = %s")
        where_params.append(value)

    where_sql = ""
    if where_clauses:
        where_sql = " WHERE " + " AND ".join(where_clauses)

    sql = f"{select_sql}{where_sql} LIMIT %s OFFSET %s"
    where_params.extend([limit, offset])

    try:
        records = await db.fetch_all(sql, tuple(where_params))
        filtered_records = _filter_records([dict(r) for r in records])
        return {
            "data": filtered_records,
            "count": len(filtered_records),
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        logger.error(f"MCP list query failed for model {model}: {e}")
        raise ValueError(f"Query failed: {str(e)}")


async def _handle_get_action(
    model: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Handle get action for a single record by ID.
    
    Params:
        id: Record ID (required)
    """
    record_id = params.get("id")
    if not record_id:
        raise ValueError("Missing required parameter: id")

    sql = _get_model_get_sql(model)
    try:
        record = await db.fetch_one(sql, (record_id,))
        if not record:
            raise ValueError(f"Record not found: {model} id={record_id}")
        
        filtered_record = _filter_sensitive_fields(dict(record))
        return {"data": filtered_record}
    except Exception as e:
        logger.error(f"MCP get query failed for model {model} id={record_id}: {e}")
        raise ValueError(f"Query failed: {str(e)}")


async def _handle_query_action(
    model: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Handle query action (similar to list but more expressive).
    
    Params:
        filters: Dict of field=value equality filters
        limit: Maximum number of records (default 50, max 100)
        offset: Offset for pagination (default 0)
    """
    # Query is essentially the same as list for security
    return await _handle_list_action(model, params)


async def _process_request(message: dict[str, Any]) -> dict[str, Any]:
    """Process an MCP request message.
    
    Expected message format:
    {
        "id": "client-generated-id",
        "action": "list|get|query",
        "model": "users|tickets|...",
        "params": {...}
    }
    
    Returns response with same id and status field.
    """
    request_id = message.get("id", str(uuid4()))
    action = message.get("action", "").lower()
    model = message.get("model", "").lower()
    params = message.get("params", {})

    # Validate model is allowed
    allowed_models = _get_allowed_models()
    if model not in allowed_models:
        return {
            "id": request_id,
            "status": "error",
            "error": f"Model '{model}' not allowed. Allowed models: {', '.join(sorted(allowed_models))}",
        }

    # Check read-only mode for write operations
    if settings.mcp_readonly and action not in {"list", "get", "query"}:
        return {
            "id": request_id,
            "status": "error",
            "error": f"Write operations not allowed in read-only mode: {action}",
        }

    # Handle supported actions
    try:
        if action == "list":
            result = await _handle_list_action(model, params)
        elif action == "get":
            result = await _handle_get_action(model, params)
        elif action == "query":
            result = await _handle_query_action(model, params)
        else:
            return {
                "id": request_id,
                "status": "error",
                "error": f"Unsupported action: {action}. Supported: list, get, query",
            }

        return {
            "id": request_id,
            "status": "ok",
            **result,
        }
    except ValueError as e:
        return {
            "id": request_id,
            "status": "error",
            "error": str(e),
        }
    except Exception as e:
        logger.exception(f"Unexpected error processing MCP request: {e}")
        return {
            "id": request_id,
            "status": "error",
            "error": "Internal server error",
        }


async def handle_mcp_connection(websocket: WebSocket) -> None:
    """Handle an MCP WebSocket connection.
    
    This is the main entry point for MCP WebSocket connections.
    It validates authentication, enforces rate limits, and processes requests.
    """
    # Validate token before accepting connection
    is_valid, error_msg = await _validate_token(websocket)
    if not is_valid:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=error_msg or "Unauthorized")
        logger.warning(f"MCP connection rejected: {error_msg}")
        return

    # Accept the connection
    await websocket.accept()
    logger.info("MCP WebSocket connection established")

    # Initialize rate limiter for this connection
    rate_limiter = RateLimiter(
        max_requests=settings.mcp_rate_limit,
        window_seconds=60,
    )

    try:
        while True:
            # Receive message
            message_text = await websocket.receive_text()
            
            # Check rate limit
            allowed, rate_error = rate_limiter.check_rate_limit()
            if not allowed:
                response = {
                    "id": None,
                    "status": "error",
                    "error": rate_error,
                }
                await websocket.send_json(response)
                # Close connection after rate limit violation
                await websocket.close(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason="Rate limit exceeded"
                )
                logger.warning(f"MCP connection closed: {rate_error}")
                break

            # Parse JSON message
            try:
                message = json.loads(message_text)
            except json.JSONDecodeError:
                response = {
                    "id": None,
                    "status": "error",
                    "error": "Invalid JSON message",
                }
                await websocket.send_json(response)
                continue

            # Process request
            response = await _process_request(message)
            
            # Send response
            await websocket.send_json(response)

    except WebSocketDisconnect:
        logger.info("MCP WebSocket connection closed by client")
    except Exception as e:
        logger.exception(f"Unexpected error in MCP WebSocket handler: {e}")
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Internal server error")
        except Exception:
            pass  # Connection may already be closed
