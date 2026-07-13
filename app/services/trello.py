from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from app.core.config import get_settings

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
TRELLO_EXTERNAL_REFERENCE_PREFIX = "trello:"

_REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class TrelloModuleDisabledError(RuntimeError):
    """Raised when the Trello integration module is disabled or not configured."""


class TrelloAuthError(RuntimeError):
    """Raised when the Trello API key or token is missing."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_credentials_for_company(company: dict[str, Any]) -> tuple[str, str]:
    """Return ``(api_key, token)`` from a company record.

    Raises :exc:`TrelloAuthError` if the credentials are blank.
    """
    api_key = str(company.get("trello_api_key") or "").strip()
    token = str(company.get("trello_token") or "").strip()
    if not api_key or not token:
        raise TrelloAuthError(
            f"Trello API key or token not configured for company {company.get('id')}"
        )
    return api_key, token


async def _get_module_enabled() -> bool:
    """Return True if the Trello module is enabled."""
    module = await module_repo.get_module(TRELLO_MODULE_SLUG)
    return bool(module and module.get("enabled"))


class _TrelloCommentHTMLParser(HTMLParser):
    """Render ticket reply HTML as Trello-friendly Markdown/plain text."""

    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "p",
        "section",
        "tr",
    }

    def __init__(self, *, image_base_url: str | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._image_base_url = (image_base_url or "").strip().rstrip("/")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        if tag_name == "br":
            self._append_newline()
            return
        if tag_name == "img":
            attr_map = {
                name.lower(): value for name, value in attrs if value is not None
            }
            src = str(attr_map.get("src") or "").strip()
            if not src:
                return
            image_url = self._absolute_image_url(src)
            alt = str(attr_map.get("alt") or "image").strip() or "image"
            self._append_newline()
            self._parts.append(f"![{alt}]({image_url})")
            self._append_newline()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self._append_newline()

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts).replace("\xa0", " ")
        lines = [line.rstrip() for line in text.splitlines()]
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

    def _append_newline(self) -> None:
        if self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")

    def _absolute_image_url(self, src: str) -> str:
        if re.match(r"^[a-z][a-z0-9+.-]*://", src, flags=re.IGNORECASE):
            return src
        if self._image_base_url:
            return urljoin(f"{self._image_base_url}/", src.lstrip("/"))
        return src


def _trello_image_base_url() -> str | None:
    """Return a configured public base URL for Trello image Markdown."""
    settings = get_settings()
    public_base = (
        str(settings.public_base_url or settings.portal_url or "").strip().rstrip("/")
    )
    if public_base and "://" not in public_base:
        public_base = f"https://{public_base}"
    return public_base or None


def _strip_html(value: str, *, image_base_url: str | None = None) -> str:
    """Convert HTML to plain text/Markdown suitable for a Trello comment."""
    if not value:
        return ""
    parser = _TrelloCommentHTMLParser(image_base_url=image_base_url)
    parser.feed(value)
    parser.close()
    return parser.get_text()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def add_comment_to_card(
    card_id: str,
    text: str,
    *,
    company: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Post a comment on a Trello card.

    The comment is prefixed with :data:`MYPORTAL_COMMENT_PREFIX` so that the
    webhook handler can identify and skip our own comments, preventing loops.

    *company* should be the full company record so that per-company credentials
    can be retrieved.  If not provided, the call is skipped.

    Returns the created comment object on success, or ``None`` on failure.
    """
    if not await _get_module_enabled():
        logger.debug("Trello add_comment_to_card skipped: module not enabled")
        return None
    if not company:
        logger.debug("Trello add_comment_to_card skipped: no company provided")
        return None
    try:
        api_key, token = await _get_credentials_for_company(company)
    except TrelloAuthError as exc:
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


async def get_card(
    card_id: str,
    *,
    company: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Fetch a Trello card by ID."""
    if not await _get_module_enabled():
        logger.debug("Trello get_card skipped: module not enabled")
        return None
    if not company:
        logger.debug("Trello get_card skipped: no company provided")
        return None
    try:
        api_key, token = await _get_credentials_for_company(company)
    except TrelloAuthError as exc:
        logger.debug("Trello get_card skipped: {}", exc)
        return None

    url = f"{TRELLO_API_BASE}/cards/{card_id}"
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.get(url, params={"key": api_key, "token": token})
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


async def list_webhooks(
    api_key: str,
    token: str,
) -> list[dict[str, Any]]:
    """Return all webhooks registered for *token*.

    Returns an empty list on failure.
    """
    url = f"{TRELLO_API_BASE}/tokens/{token}/webhooks"
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.get(url, params={"key": api_key})
            response.raise_for_status()
            return response.json() or []
    except Exception as exc:
        logger.warning("Trello list_webhooks error: {}", exc)
        return []


async def delete_webhook(
    webhook_id: str,
    api_key: str,
    token: str,
) -> bool:
    """Delete a Trello webhook by ID.

    Returns ``True`` on success, ``False`` otherwise.
    """
    url = f"{TRELLO_API_BASE}/webhooks/{webhook_id}"
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.delete(url, params={"key": api_key, "token": token})
            response.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("Trello delete_webhook {} error: {}", webhook_id, exc)
        return False


async def register_webhook(
    board_id: str,
    callback_url: str,
    *,
    company: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Register a Trello webhook for *board_id* pointing to *callback_url*.

    *company* must be the full company record with per-company Trello credentials.

    If Trello reports that a webhook with the same callback/model/token already
    exists (HTTP 400), the function looks up the existing webhook for this board:

    * If it already points to *callback_url* it is returned as-is (idempotent).
    * If it points to a *different* URL (e.g. the old ``http://`` address before
      an HTTP→HTTPS migration) the stale webhook is deleted and a fresh one is
      created with the new *callback_url*.

    Returns the webhook object on success, or ``None`` on failure.
    """
    if not await _get_module_enabled():
        logger.debug("Trello register_webhook skipped: module not enabled")
        return None
    if not company:
        logger.debug("Trello register_webhook skipped: no company provided")
        return None
    try:
        api_key, token = await _get_credentials_for_company(company)
    except TrelloAuthError as exc:
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
        if exc.response.status_code == 400 and "already exists" in exc.response.text:
            # A webhook for this board/token combination already exists.
            # Resolve it: find the existing webhook and either return it (if the
            # callback URL matches) or replace it (if it differs, e.g. after an
            # HTTP→HTTPS migration).
            logger.info(
                "Trello register_webhook: 400 'already exists' for board {}; "
                "looking up existing webhooks to reconcile",
                board_id,
            )
            existing = await list_webhooks(api_key, token)
            for hook in existing:
                if str(hook.get("idModel") or "") == board_id:
                    existing_callback = str(hook.get("callbackURL") or "")
                    if existing_callback == callback_url:
                        # Already correct – treat as success.
                        logger.info(
                            "Trello register_webhook: webhook {} already has "
                            "correct callback URL; returning existing",
                            hook.get("id"),
                        )
                        return hook
                    # Stale URL (e.g. old http:// address) – delete and re-register.
                    logger.info(
                        "Trello register_webhook: replacing stale webhook {} "
                        "(old callback: {}) with {}",
                        hook.get("id"),
                        existing_callback,
                        callback_url,
                    )
                    deleted = await delete_webhook(str(hook["id"]), api_key, token)
                    if not deleted:
                        logger.warning(
                            "Trello register_webhook: failed to delete stale webhook {}; "
                            "attempting re-registration anyway",
                            hook.get("id"),
                        )
                    try:
                        async with httpx.AsyncClient(
                            timeout=_REQUEST_TIMEOUT
                        ) as client:
                            retry = await client.post(
                                url,
                                params={"key": api_key, "token": token},
                                json={
                                    "callbackURL": callback_url,
                                    "idModel": board_id,
                                    "description": "MyPortal Trello Integration",
                                },
                            )
                            retry.raise_for_status()
                            return retry.json()
                    except httpx.HTTPStatusError as retry_exc:
                        logger.error(
                            "Trello register_webhook retry failed: HTTP {} {}",
                            retry_exc.response.status_code,
                            retry_exc.response.text[:200],
                        )
                        return None
                    except Exception as retry_exc:
                        logger.error(
                            "Trello register_webhook retry error: {}", retry_exc
                        )
                        return None
            # Could not find the conflicting webhook – fall through to generic error.
            logger.error(
                "Trello register_webhook failed: HTTP {} {}",
                exc.response.status_code,
                exc.response.text[:200],
            )
            return None
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


def card_external_reference(card_id: str) -> str:
    """Return the canonical ticket external reference for a Trello card ID."""
    card_id = str(card_id or "").strip()
    if card_id.startswith(TRELLO_EXTERNAL_REFERENCE_PREFIX):
        return card_id
    return f"{TRELLO_EXTERNAL_REFERENCE_PREFIX}{card_id}"


def card_id_from_external_reference(external_reference: Any) -> str | None:
    """Extract a Trello card ID from a ticket external reference.

    Trello ticket references are stored as ``trello:<card_id>`` so automation
    rules can target Trello-specific references. Legacy tickets may still carry
    only the raw card ID; callers that already know the ticket belongs to the
    Trello module can fall back to that value.
    """
    value = str(external_reference or "").strip()
    if not value:
        return None
    if value.startswith(TRELLO_EXTERNAL_REFERENCE_PREFIX):
        card_id = value[len(TRELLO_EXTERNAL_REFERENCE_PREFIX) :].strip()
        return card_id or None
    return value


async def find_ticket_for_card(card_id: str) -> dict[str, Any] | None:
    """Return the MyPortal ticket linked to a Trello card ID."""
    canonical_ref = card_external_reference(card_id)
    ticket = await tickets_repo.get_ticket_by_external_reference(canonical_ref)
    if ticket:
        return ticket
    # Backward compatibility for tickets created before Trello references were
    # namespaced. This can be removed after legacy data has been migrated.
    legacy_card_id = str(card_id or "").strip()
    if legacy_card_id and legacy_card_id != canonical_ref:
        return await tickets_repo.get_ticket_by_external_reference(legacy_card_id)
    return None


async def post_ticket_created_comment(
    card_id: str,
    ticket_number: str | int,
    *,
    company: dict[str, Any] | None = None,
) -> None:
    """Post a 'Support Ticket Created' confirmation comment on a Trello card."""
    text = f"Support Ticket Created - Ticket #{ticket_number}"
    await add_comment_to_card(card_id, text, company=company)


async def post_reply_comment(
    card_id: str,
    author_display_name: str | None,
    reply_html: str,
    *,
    company: dict[str, Any] | None = None,
) -> None:
    """Post a ticket reply as a comment on a Trello card."""
    plain_body = _strip_html(reply_html, image_base_url=_trello_image_base_url())
    if not plain_body:
        logger.debug(
            "Trello post_reply_comment: skipping empty body for card {}", card_id
        )
        return
    author_label = author_display_name or "Staff"
    text = f"{author_label}: {plain_body}"
    await add_comment_to_card(card_id, text, company=company)


async def validate_credentials_for_company(company: dict[str, Any]) -> dict[str, Any]:
    """Validate Trello credentials for a company by calling the /members/me endpoint.

    Returns a dict with ``status`` of ``"ok"`` or ``"error"``.
    """
    if not await _get_module_enabled():
        return {"status": "error", "message": "Trello module is not enabled"}
    try:
        api_key, token = await _get_credentials_for_company(company)
    except TrelloAuthError as exc:
        return {"status": "error", "message": str(exc)}

    url = f"{TRELLO_API_BASE}/members/me"
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.get(url, params={"key": api_key, "token": token})
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


async def validate_credentials() -> dict[str, Any]:
    """Kept for backwards compatibility; validate is now per-company.

    Returns an informational message directing admins to configure credentials
    on each company's edit page.
    """
    module = await module_repo.get_module(TRELLO_MODULE_SLUG)
    if not module or not module.get("enabled"):
        return {"status": "error", "message": "Trello module is not enabled"}
    return {
        "status": "ok",
        "message": "Trello module is enabled. API credentials are configured per company.",
    }
