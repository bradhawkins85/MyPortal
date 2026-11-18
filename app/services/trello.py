from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from app.core.config import get_settings
from app.security.encryption import decrypt_secret, encrypt_secret

import httpx

from app.services.monitored_http import monitored_client
from loguru import logger

from app.repositories import companies as company_repo
from app.repositories import integration_modules as module_repo
from app.repositories import tickets as tickets_repo

TRELLO_MODULE_SLUG = "trello"
TRELLO_API_BASE = "https://api.trello.com/1"

# Trello OAuth 2.0 (Atlassian 3LO) endpoints and scopes.
TRELLO_OAUTH_AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TRELLO_OAUTH_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
TRELLO_OAUTH_SCOPES = "read:board:trello write:board:trello offline_access"
TRELLO_OAUTH_CALLBACK_PATH = "/trello/callback"
# Refresh the access token this long before it actually expires.
_TOKEN_REFRESH_MARGIN = timedelta(seconds=60)

# Prefix added to every comment MyPortal posts to Trello.  The webhook handler
# skips incoming comments that carry this prefix to prevent feedback loops.
MYPORTAL_COMMENT_PREFIX = "[MyPortal]"
TRELLO_EXTERNAL_REFERENCE_PREFIX = "trello:"

_REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class TrelloModuleDisabledError(RuntimeError):
    """Raised when the Trello integration module is disabled or not configured."""


class TrelloAuthError(RuntimeError):
    """Raised when the Trello API key or token is missing."""


class TrelloOAuthError(RuntimeError):
    """Raised when a Trello OAuth 2.0 token exchange or refresh fails."""


@dataclass(frozen=True)
class TrelloAuth:
    """Credentials for one Trello API call.

    ``mode`` is ``"oauth"`` (OAuth 2.0 bearer token) or ``"legacy"``
    (API key + token query parameters).
    """

    mode: str
    params: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    api_key: str = ""
    token: str = ""

    @property
    def is_oauth(self) -> bool:
        return self.mode == "oauth"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


# Trello IDs are 24-character lowercase hex strings; checking them with
# ``fullmatch`` stops user-supplied data from altering the URL path.
_TRELLO_ID_RE = re.compile(r"[0-9a-f]{24}")


async def _get_credentials_for_company(company: dict[str, Any]) -> tuple[str, str]:
    """Return the legacy ``(api_key, token)`` pair from a company record.

    Raises :exc:`TrelloAuthError` if the credentials are blank.
    """
    api_key = str(company.get("trello_api_key") or "").strip()
    token = str(company.get("trello_token") or "").strip()
    if not api_key or not token:
        raise TrelloAuthError(
            f"Trello API key or token not configured for company {company.get('id')}"
        )
    return api_key, token


def _decrypt_company_secret(company: dict[str, Any], column: str) -> str:
    value = str(company.get(column) or "").strip()
    if not value:
        return ""
    try:
        return decrypt_secret(value, allow_plaintext=False)
    except Exception as exc:
        logger.warning(
            "Trello: unable to decrypt {} for company {}: {}", column, company.get("id"), exc
        )
        return ""


def _parse_expiry(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def company_has_oauth_connection(company: dict[str, Any] | None) -> bool:
    """Return True when the company has connected Trello via OAuth 2.0."""
    if not company:
        return False
    return bool(
        str(company.get("trello_oauth_access_token") or "").strip()
        or str(company.get("trello_oauth_refresh_token") or "").strip()
    )


_refresh_locks: dict[int, asyncio.Lock] = {}


def _refresh_lock(company_id: int) -> asyncio.Lock:
    lock = _refresh_locks.get(company_id)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[company_id] = lock
    return lock


async def get_oauth_client_settings() -> dict[str, str]:
    """Return the OAuth 2.0 client ID/secret from the Trello module settings."""
    from app.services import modules as modules_service

    try:
        settings = await modules_service.get_module_settings(TRELLO_MODULE_SLUG) or {}
    except Exception as exc:
        logger.warning("Trello: unable to load module settings: {}", exc)
        settings = {}
    client_id = str(
        settings.get("oauth_client_id") or os.getenv("TRELLO_OAUTH_CLIENT_ID", "")
    ).strip()
    client_secret = str(
        settings.get("oauth_client_secret") or os.getenv("TRELLO_OAUTH_CLIENT_SECRET", "")
    ).strip()
    return {"client_id": client_id, "client_secret": client_secret}


def build_oauth_redirect_uri(request_base_url: str | None = None) -> str:
    """Return the OAuth 2.0 callback URL to register with Trello/Atlassian.

    ``PUBLIC_BASE_URL`` wins, then ``PORTAL_URL``, then the request base URL.
    """
    settings = get_settings()
    base = str(settings.public_base_url or settings.portal_url or "").strip().rstrip("/")
    if base and "://" not in base:
        base = f"https://{base}"
    if not base:
        base = str(request_base_url or "").strip().rstrip("/")
    return f"{base}{TRELLO_OAUTH_CALLBACK_PATH}"


def generate_pkce_pair() -> tuple[str, str]:
    """Return a PKCE ``(code_verifier, code_challenge)`` pair using S256."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(
    *, client_id: str, redirect_uri: str, state: str, code_challenge: str
) -> str:
    """Return the Atlassian authorize URL that starts the Trello OAuth 2.0 flow."""
    from urllib.parse import urlencode

    params = {
        "client_id": client_id,
        "scope": TRELLO_OAUTH_SCOPES,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "prompt": "consent",
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }
    return f"{TRELLO_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


async def _request_token(payload: dict[str, str]) -> dict[str, Any]:
    try:
        async with monitored_client(httpx.AsyncClient, timeout=_REQUEST_TIMEOUT) as client:
            response = await client.post(
                TRELLO_OAUTH_TOKEN_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise TrelloOAuthError(
            f"Trello token endpoint returned HTTP {exc.response.status_code}"
        ) from exc
    except Exception as exc:
        raise TrelloOAuthError(f"Trello token request failed: {exc}") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise TrelloOAuthError("Trello token response did not include an access token")
    return data


def _expiry_from_response(data: dict[str, Any]) -> datetime | None:
    expires_in = data.get("expires_in")
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


async def _store_tokens(
    company_id: int, data: dict[str, Any], *, refresh_fallback: str = "", member: str | None
) -> tuple[str, datetime | None]:
    access_token = str(data.get("access_token") or "")
    refresh_token = str(data.get("refresh_token") or refresh_fallback or "")
    expires_at = _expiry_from_response(data)
    await company_repo.set_trello_oauth_tokens(
        company_id,
        access_token=encrypt_secret(access_token) if access_token else None,
        refresh_token=encrypt_secret(refresh_token) if refresh_token else None,
        expires_at=expires_at.replace(tzinfo=None) if expires_at else None,
        member=member,
    )
    return access_token, expires_at


async def exchange_authorization_code(
    company_id: int, *, code: str, code_verifier: str, redirect_uri: str
) -> dict[str, Any]:
    """Exchange an authorization code for tokens and store them on the company.

    Returns ``{"member": <username or None>}``.
    """
    client = await get_oauth_client_settings()
    if not client["client_id"] or not client["client_secret"]:
        raise TrelloOAuthError("Trello OAuth client ID and secret are not configured")
    data = await _request_token(
        {
            "grant_type": "authorization_code",
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
    )
    member = await _fetch_member_name(str(data.get("access_token")))
    await _store_tokens(company_id, data, member=member)
    return {"member": member}


async def _fetch_member_name(access_token: str) -> str | None:
    try:
        async with monitored_client(httpx.AsyncClient, timeout=_REQUEST_TIMEOUT) as client:
            response = await client.get(
                f"{TRELLO_API_BASE}/members/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            data = response.json() or {}
    except Exception as exc:
        logger.warning("Trello: unable to look up connected member: {}", exc)
        return None
    name = str(data.get("username") or data.get("fullName") or "").strip()
    return name[:255] or None


async def disconnect_oauth(company_id: int) -> None:
    """Forget the company's Trello OAuth 2.0 tokens."""
    await company_repo.set_trello_oauth_tokens(
        company_id, access_token=None, refresh_token=None, expires_at=None, member=None
    )


async def _refresh_access_token(company: dict[str, Any]) -> str:
    company_id = int(company["id"])
    refresh_token = _decrypt_company_secret(company, "trello_oauth_refresh_token")
    if not refresh_token:
        raise TrelloAuthError(
            f"Trello OAuth connection for company {company_id} has expired; reconnect Trello"
        )
    client = await get_oauth_client_settings()
    if not client["client_id"] or not client["client_secret"]:
        raise TrelloAuthError("Trello OAuth client ID and secret are not configured")
    try:
        data = await _request_token(
            {
                "grant_type": "refresh_token",
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "refresh_token": refresh_token,
            }
        )
    except TrelloOAuthError as exc:
        raise TrelloAuthError(f"Trello OAuth refresh failed for company {company_id}: {exc}") from exc
    access_token, _ = await _store_tokens(
        company_id,
        data,
        refresh_fallback=refresh_token,
        member=company.get("trello_oauth_member"),
    )
    logger.info("Trello: refreshed OAuth access token for company {}", company_id)
    return access_token


async def _get_oauth_access_token(company: dict[str, Any]) -> str:
    company_id = int(company["id"])
    access_token = _decrypt_company_secret(company, "trello_oauth_access_token")
    expires_at = _parse_expiry(company.get("trello_oauth_expires_at"))
    now = datetime.now(timezone.utc)
    if access_token and (expires_at is None or expires_at - _TOKEN_REFRESH_MARGIN > now):
        return access_token
    async with _refresh_lock(company_id):
        # Another task may have refreshed while we waited; reload the record.
        fresh = await company_repo.get_company_by_id(company_id) or company
        access_token = _decrypt_company_secret(fresh, "trello_oauth_access_token")
        expires_at = _parse_expiry(fresh.get("trello_oauth_expires_at"))
        if access_token and expires_at and expires_at - _TOKEN_REFRESH_MARGIN > now:
            return access_token
        return await _refresh_access_token(fresh)


async def get_auth_for_company(company: dict[str, Any]) -> TrelloAuth:
    """Return credentials for *company*, preferring an OAuth 2.0 connection.

    Falls back to the legacy API key/token pair. Raises :exc:`TrelloAuthError`
    when neither is available.
    """
    if company_has_oauth_connection(company):
        access_token = await _get_oauth_access_token(company)
        return TrelloAuth(
            mode="oauth", headers={"Authorization": f"Bearer {access_token}"}
        )
    api_key, token = await _get_credentials_for_company(company)
    return TrelloAuth(
        mode="legacy",
        params={"key": api_key, "token": token},
        api_key=api_key,
        token=token,
    )


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

    # Rich-text replies can reach this integration with their markup entity-
    # encoded, sometimes more than once after passing through the editor and
    # sanitiser (for example ``&amp;lt;div&amp;gt;``). HTMLParser decodes
    # character references while emitting text but does not parse the decoded
    # text as markup, which would leak ``<div><br></div>`` into Trello. Decode
    # boundedly before parsing so those tags are handled as HTML instead.
    for _ in range(3):
        decoded = unescape(value)
        if decoded == value:
            break
        value = decoded
    parser = _TrelloCommentHTMLParser(image_base_url=image_base_url)
    parser.feed(value)
    parser.close()
    return parser.get_text()


def _canonical_webhook_callback_url(callback_url: str) -> str:
    """Return a normalized callback URL for webhook de-duplication."""
    value = str(callback_url or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower() or "https"
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = hostname
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{netloc}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, "", ""))


def _same_callback_except_scheme(left: str, right: str) -> bool:
    """Return true when two callback URLs only differ by http/https scheme."""
    left_canonical = _canonical_webhook_callback_url(left)
    right_canonical = _canonical_webhook_callback_url(right)
    if not left_canonical or not right_canonical:
        return False
    left_parts = urlsplit(left_canonical)
    right_parts = urlsplit(right_canonical)
    return (
        left_parts.netloc == right_parts.netloc
        and left_parts.path == right_parts.path
        and left_parts.query == right_parts.query
    )


async def _reconcile_existing_webhooks(
    board_id: str,
    callback_url: str,
    auth: TrelloAuth,
) -> dict[str, Any] | None:
    """Return the matching webhook and remove stale http:// duplicates."""
    existing = await list_webhooks(auth)
    matching_hook: dict[str, Any] | None = None
    stale_ids: list[str] = []
    for hook in existing:
        if str(hook.get("idModel") or "") != board_id:
            continue
        existing_callback = str(hook.get("callbackURL") or "")
        if _canonical_webhook_callback_url(existing_callback) == _canonical_webhook_callback_url(
            callback_url
        ):
            matching_hook = hook
            continue
        if _same_callback_except_scheme(existing_callback, callback_url):
            hook_id = str(hook.get("id") or "").strip()
            if hook_id:
                stale_ids.append(hook_id)

    for hook_id in stale_ids:
        logger.info(
            "Trello register_webhook: deleting stale duplicate webhook {} for board {}",
            hook_id,
            board_id,
        )
        await delete_webhook(hook_id, auth)

    return matching_hook


async def _remove_legacy_webhooks(
    board_id: str, callback_url: str, company: dict[str, Any]
) -> None:
    """Delete webhooks this board still has under the legacy key/token.

    Once a company connects with OAuth 2.0, a webhook left behind under the
    legacy token would deliver every event a second time.
    """
    try:
        api_key, token = await _get_credentials_for_company(company)
    except TrelloAuthError:
        return
    legacy = TrelloAuth(
        mode="legacy", params={"key": api_key, "token": token}, api_key=api_key, token=token
    )
    for hook in await list_webhooks(legacy):
        if str(hook.get("idModel") or "") != board_id:
            continue
        existing_callback = str(hook.get("callbackURL") or "")
        if _canonical_webhook_callback_url(existing_callback) == _canonical_webhook_callback_url(
            callback_url
        ) or _same_callback_except_scheme(existing_callback, callback_url):
            hook_id = str(hook.get("id") or "").strip()
            if hook_id:
                logger.info(
                    "Trello register_webhook: removing legacy-token webhook {} for board {}",
                    hook_id,
                    board_id,
                )
                await delete_webhook(hook_id, legacy)


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
    if not isinstance(card_id, str) or not _TRELLO_ID_RE.fullmatch(card_id):
        logger.error("Trello add_comment_to_card skipped: invalid card_id {}", card_id)
        return None
    try:
        auth = await get_auth_for_company(company)
    except TrelloAuthError as exc:
        logger.debug("Trello add_comment_to_card skipped: {}", exc)
        return None

    full_text = f"{MYPORTAL_COMMENT_PREFIX} {text}"
    url = f"{TRELLO_API_BASE}/cards/{quote(card_id, safe='')}/actions/comments"
    try:
        async with monitored_client(httpx.AsyncClient, timeout=_REQUEST_TIMEOUT) as client:
            response = await client.post(
                url,
                params=auth.params,
                headers=auth.headers,
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
    if not isinstance(card_id, str) or not _TRELLO_ID_RE.fullmatch(card_id):
        logger.error("Trello get_card skipped: invalid card_id {}", card_id)
        return None
    try:
        auth = await get_auth_for_company(company)
    except TrelloAuthError as exc:
        logger.debug("Trello get_card skipped: {}", exc)
        return None

    url = f"{TRELLO_API_BASE}/cards/{card_id}"
    try:
        async with monitored_client(httpx.AsyncClient, timeout=_REQUEST_TIMEOUT) as client:
            response = await client.get(url, params=auth.params, headers=auth.headers)
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


async def list_webhooks(auth: TrelloAuth) -> list[dict[str, Any]]:
    """Return all webhooks registered for a legacy Trello *token*.

    Trello only exposes the per-token listing for legacy key/token pairs, so
    OAuth 2.0 connections always return an empty list. Returns an empty list
    on failure.
    """
    if auth.is_oauth or not auth.token:
        return []
    url = f"{TRELLO_API_BASE}/tokens/{quote(auth.token, safe='')}/webhooks"
    try:
        async with monitored_client(httpx.AsyncClient, timeout=_REQUEST_TIMEOUT) as client:
            response = await client.get(url, params={"key": auth.api_key})
            response.raise_for_status()
            return response.json() or []
    except Exception as exc:
        logger.warning("Trello list_webhooks error: {}", exc)
        return []


async def delete_webhook(webhook_id: str, auth: TrelloAuth) -> bool:
    """Delete a Trello webhook by ID.

    Returns ``True`` on success, ``False`` otherwise.
    """
    url = f"{TRELLO_API_BASE}/webhooks/{quote(str(webhook_id), safe='')}"
    try:
        async with monitored_client(httpx.AsyncClient, timeout=_REQUEST_TIMEOUT) as client:
            response = await client.delete(url, params=auth.params, headers=auth.headers)
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
        auth = await get_auth_for_company(company)
    except TrelloAuthError as exc:
        logger.debug("Trello register_webhook skipped: {}", exc)
        return None

    if auth.is_oauth:
        await _remove_legacy_webhooks(board_id, callback_url, company)

    existing_hook = await _reconcile_existing_webhooks(board_id, callback_url, auth)
    if existing_hook:
        logger.info(
            "Trello register_webhook: webhook {} already has correct callback URL",
            existing_hook.get("id"),
        )
        return existing_hook

    url = f"{TRELLO_API_BASE}/webhooks"
    try:
        async with monitored_client(httpx.AsyncClient, timeout=_REQUEST_TIMEOUT) as client:
            response = await client.post(
                url,
                params=auth.params,
                headers=auth.headers,
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
            if auth.is_oauth:
                # OAuth 2.0 webhooks belong to the member's grant and cannot
                # be listed per token; an existing one already delivers here.
                logger.info(
                    "Trello register_webhook: OAuth webhook for board {} already exists",
                    board_id,
                )
                return {
                    "idModel": board_id,
                    "callbackURL": callback_url,
                    "active": True,
                    "already_registered": True,
                }
            existing = await list_webhooks(auth)
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
                    deleted = await delete_webhook(str(hook["id"]), auth)
                    if not deleted:
                        logger.warning(
                            "Trello register_webhook: failed to delete stale webhook {}; "
                            "attempting re-registration anyway",
                            hook.get("id"),
                        )
                    try:
                        async with monitored_client(httpx.AsyncClient,
                            timeout=_REQUEST_TIMEOUT
                        ) as client:
                            retry = await client.post(
                                url,
                                params=auth.params,
                                headers=auth.headers,
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
        auth = await get_auth_for_company(company)
    except TrelloAuthError as exc:
        return {"status": "error", "message": str(exc)}

    url = f"{TRELLO_API_BASE}/members/me"
    try:
        async with monitored_client(httpx.AsyncClient, timeout=_REQUEST_TIMEOUT) as client:
            response = await client.get(url, params=auth.params, headers=auth.headers)
        if response.status_code == 401:
            if auth.is_oauth:
                return {
                    "status": "error",
                    "message": "Trello rejected the OAuth connection; reconnect Trello",
                }
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
    client = await get_oauth_client_settings()
    if client["client_id"] and client["client_secret"]:
        return {
            "status": "ok",
            "message": (
                "Trello module is enabled with OAuth 2.0. Connect each company to "
                "Trello from its edit page."
            ),
        }
    return {
        "status": "ok",
        "message": "Trello module is enabled. API credentials are configured per company.",
    }
