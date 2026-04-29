from __future__ import annotations

import html
import re
from typing import Any, Mapping

import httpx
from loguru import logger

from app.repositories import companies as company_repo
from app.repositories import integration_modules as module_repo
from app.repositories import tickets as tickets_repo

TRELLO_MODULE_SLUG = "trello"
TRELLO_API_BASE = "https://api.trello.com/1"

# Prefix added to every comment MyPortal posts to Trello.  The webhook handler
# skips incoming comments that carry this prefix to prevent feedback loops.
MYPORTAL_COMMENT_PREFIX = "[MyPortal]"

_REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class TrelloModuleDisabledError(RuntimeError):
    """Raised when the Trello integration module is disabled or not configured."""


class TrelloAuthError(RuntimeError):
    """Raised when the Trello API key or token is missing."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_credentials() -> tuple[str, str]:
    """Return ``(api_key, token)`` from module settings.

    Raises :exc:`TrelloModuleDisabledError` if the module is absent or
    disabled, and :exc:`TrelloAuthError` if the credentials are blank.
    """
    module = await module_repo.get_module(TRELLO_MODULE_SLUG)
    if not module or not module.get("enabled"):
        raise TrelloModuleDisabledError("Trello module is not enabled")
    settings = module.get("settings") or {}
    api_key = str(settings.get("api_key") or "").strip()
    token = str(settings.get("token") or "").strip()
    if not api_key or not token:
        raise TrelloAuthError("Trello API key or token not configured")
    return api_key, token


def _strip_html(value: str) -> str:
    """Convert simple HTML to plain text suitable for a Trello comment."""
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|tr)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    lines = [line.rstrip() for line in text.splitlines()]
    # Collapse consecutive blank lines
    result: list[str] = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank:
                result.append("")
            prev_blank = True
        else:
            result.append(line)
            prev_blank = False
    return "\n".join(result).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def add_comment_to_card(card_id: str, text: str) -> dict[str, Any] | None:
    """Post a comment on a Trello card.

    The comment is prefixed with :data:`MYPORTAL_COMMENT_PREFIX` so that the
    webhook handler can identify and skip our own comments, preventing loops.

    Returns the created comment object on success, or ``None`` on failure.
    """
    try:
        api_key, token = await _get_credentials()
    except (TrelloModuleDisabledError, TrelloAuthError) as exc:
        logger.debug("Trello add_comment_to_card skipped: {}", exc)
        return None

    full_text = f"{MYPORTAL_COMMENT_PREFIX} {text}"
    url = f"{TRELLO_API_BASE}/cards/{card_id}/actions/comments"
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.post(
                url,
                params={"key": api_key, "token": token},
                json={"text": full_text},
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Trello add_comment_to_card failed: HTTP {} {}",
            exc.response.status_code,
            exc.response.text[:200],
        )
        return None
    except Exception as exc:
        logger.error("Trello add_comment_to_card error: {}", exc)
        return None


async def get_card(card_id: str) -> dict[str, Any] | None:
    """Fetch a Trello card by ID."""
    try:
        api_key, token = await _get_credentials()
    except (TrelloModuleDisabledError, TrelloAuthError) as exc:
        logger.debug("Trello get_card skipped: {}", exc)
        return None

    url = f"{TRELLO_API_BASE}/cards/{card_id}"
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.get(
                url, params={"key": api_key, "token": token}
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Trello get_card failed: HTTP {} {}",
            exc.response.status_code,
            exc.response.text[:200],
        )
        return None
    except Exception as exc:
        logger.error("Trello get_card error: {}", exc)
        return None


async def register_webhook(board_id: str, callback_url: str) -> dict[str, Any] | None:
    """Register a Trello webhook for *board_id* pointing to *callback_url*.

    Returns the created webhook object on success, or ``None`` on failure.
    """
    try:
        api_key, token = await _get_credentials()
    except (TrelloModuleDisabledError, TrelloAuthError) as exc:
        logger.debug("Trello register_webhook skipped: {}", exc)
        return None

    url = f"{TRELLO_API_BASE}/webhooks"
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.post(
                url,
                params={"key": api_key, "token": token},
                json={
                    "callbackURL": callback_url,
                    "idModel": board_id,
                    "description": "MyPortal Trello Integration",
                },
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Trello register_webhook failed: HTTP {} {}",
            exc.response.status_code,
            exc.response.text[:200],
        )
        return None
    except Exception as exc:
        logger.error("Trello register_webhook error: {}", exc)
        return None


async def get_company_for_board(board_id: str) -> dict[str, Any] | None:
    """Return the company record linked to *board_id*, or ``None``."""
    row = await company_repo.get_company_by_trello_board_id(board_id)
    return row


async def find_ticket_for_card(card_id: str) -> dict[str, Any] | None:
    """Return the MyPortal ticket whose ``external_reference`` matches *card_id*."""
    return await tickets_repo.get_ticket_by_external_reference(card_id)


async def post_ticket_created_comment(card_id: str, ticket_number: str | int) -> None:
    """Post a 'Support Ticket Created' confirmation comment on a Trello card."""
    text = f"Support Ticket Created - Ticket #{ticket_number}"
    await add_comment_to_card(card_id, text)


async def post_reply_comment(
    card_id: str,
    author_display_name: str | None,
    reply_html: str,
) -> None:
    """Post a ticket reply as a comment on a Trello card."""
    plain_body = _strip_html(reply_html)
    if not plain_body:
        return
    author_label = author_display_name or "Staff"
    text = f"{author_label}: {plain_body}"
    await add_comment_to_card(card_id, text)


async def validate_credentials() -> dict[str, Any]:
    """Validate Trello credentials by calling the /members/me endpoint.

    Returns a dict with ``status`` of ``"ok"`` or ``"error"``.
    """
    try:
        api_key, token = await _get_credentials()
    except TrelloModuleDisabledError as exc:
        return {"status": "error", "message": str(exc)}
    except TrelloAuthError as exc:
        return {"status": "error", "message": str(exc)}

    url = f"{TRELLO_API_BASE}/members/me"
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.get(
                url, params={"key": api_key, "token": token}
            )
        if response.status_code == 401:
            return {"status": "error", "message": "Invalid Trello API key or token"}
        response.raise_for_status()
        data = response.json()
        username = data.get("username") or data.get("fullName") or "unknown"
        return {"status": "ok", "message": f"Connected as @{username}"}
    except httpx.HTTPStatusError as exc:
        return {
            "status": "error",
            "message": f"Trello API returned HTTP {exc.response.status_code}",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Failed to connect to Trello: {exc}"}
