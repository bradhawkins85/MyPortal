from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from loguru import logger


def build_client_error_response(detail: str, error_id: str) -> dict[str, str]:
    """Return a stable, client-safe API error contract payload."""
    return {"detail": detail, "error_id": error_id}


def new_error_id() -> str:
    return uuid4().hex


def log_exception_with_error_id(message: str, *, error_id: str, **context: Any) -> None:
    """Log full exception + traceback while binding an error reference ID."""
    logger.bind(error_id=error_id, **context).exception(message)


def build_client_http_error(status_code: int, detail: str, *, error_id: str | None = None) -> HTTPException:
    """
    Build an HTTPException using the shared safe error payload contract.

    Contributor guideline:
    - Forbidden: returning raw `str(exc)` from unexpected exceptions (DB driver/provider/stack details may leak).
    - Allowed: explicit, user-safe validation or business-rule messages that are intentionally crafted for clients.
    """
    resolved_error_id = error_id or new_error_id()
    return HTTPException(
        status_code=status_code,
        detail=build_client_error_response(detail=detail, error_id=resolved_error_id),
    )

