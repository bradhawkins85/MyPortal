from __future__ import annotations

import secrets
from typing import Iterable
from urllib.parse import quote, urlsplit

from fastapi import Request
from starlette.datastructures import FormData
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.core.config import get_settings
from app.core.logging import log_warning
from app.security.session import SessionManager, session_manager

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Multipart parsers default to a 1 MiB limit for non-file fields. Rich-text
# ticket replies can legitimately exceed that when a pasted image is encoded in
# the ``body`` field before it is persisted as an attachment. Keep the limit
# bounded while allowing the base64 representation of a maximum-size (50 MiB)
# ticket attachment plus encoding and markup overhead.
MAX_CSRF_FORM_PART_SIZE = 70 * 1024 * 1024
DEFAULT_EXEMPT_PREFIXES = (
    "/auth/login",
    "/auth/passkeys/authenticate",
    "/auth/register",
    "/auth/password/forgot",
    "/auth/password/reset",
    "/api/knowledge-base/search",
)


LOGIN_PATH = "/login"
SESSION_EXPIRED_HEADER = "X-Session-Expired"


def _is_browser_navigation(request: Request) -> bool:
    """Return True for top-level browser form submissions (not fetch/XHR/htmx)."""

    fetch_mode = request.headers.get("sec-fetch-mode", "").lower()
    if fetch_mode:
        return fetch_mode == "navigate"
    if request.headers.get("hx-request") or request.headers.get("x-requested-with"):
        return False
    return "text/html" in request.headers.get("accept", "").lower()


def _login_url_for(request: Request) -> str:
    """Build the login URL, returning the user to the page they submitted from."""

    referer = request.headers.get("referer")
    if not referer:
        return LOGIN_PATH
    try:
        parts = urlsplit(referer)
    except ValueError:
        return LOGIN_PATH
    if parts.netloc and parts.netloc != request.url.netloc:
        return LOGIN_PATH
    target = parts.path or "/"
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return LOGIN_PATH
    if target == "/" or target == LOGIN_PATH or target.startswith(("/login/", "/logout")):
        return LOGIN_PATH
    if parts.query:
        target = f"{target}?{parts.query}"
    return f"{LOGIN_PATH}?next={quote(target, safe='')}"


def session_expired_response(request: Request) -> Response:
    """Send the user back to sign in when their session has expired.

    Browser form submissions are redirected straight to the login page. Script
    driven requests (fetch/XHR/htmx) receive a 401 with headers that the
    frontend uses to redirect, since a redirect would be followed silently.
    """

    login_url = _login_url_for(request)
    if _is_browser_navigation(request):
        return RedirectResponse(url=login_url, status_code=303)
    return JSONResponse(
        status_code=401,
        content={"detail": "Session expired. Please sign in again.", "login_url": login_url},
        headers={SESSION_EXPIRED_HEADER: "1", "HX-Redirect": login_url},
    )


async def parse_csrf_form(request: Request) -> FormData:
    """Parse a form with enough bounded capacity for base64 inline images."""
    try:
        return await request.form(max_part_size=MAX_CSRF_FORM_PART_SIZE)
    except TypeError:  # pragma: no cover - compatibility with older Starlette
        return await request.form()


class CSRFMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        manager: SessionManager | None = None,
        exempt_paths: Iterable[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._session_manager = manager or session_manager
        self._exempt_paths = tuple(exempt_paths or ())
        self._settings = get_settings()

    async def dispatch(self, request: Request, call_next):
        if not self._settings.enable_csrf:
            return await call_next(request)

        # API clients that authenticate with bearer tokens or API keys are not
        # vulnerable to CSRF because browsers will not automatically attach
        # those credentials. Skip CSRF validation ONLY when no session cookie
        # is present; if a session cookie is also present (for example because
        # the user is logged in in the same browser), require CSRF regardless
        # of any attacker-controlled Authorization/X-API-Key header – this
        # prevents a CSRF bypass where the attacker supplies a spoofed header
        # to opt themselves out of the check.
        session_cookie_name = self._settings.session_cookie_name
        has_session_cookie = bool(request.cookies.get(session_cookie_name))
        authorization_header = request.headers.get("authorization", "")
        has_token_auth = (
            authorization_header.lower().startswith("bearer ")
            or bool(request.headers.get("x-api-key"))
        )
        if has_token_auth and not has_session_cookie:
            return await call_next(request)

        if request.method.upper() in SAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(prefix) for prefix in (*DEFAULT_EXEMPT_PREFIXES, *self._exempt_paths)):
            return await call_next(request)

        header_token = (
            request.headers.get("X-CSRF-Token")
            or request.headers.get("X-CSRFToken")
            or request.headers.get("CSRF-Token")
        )

        if not header_token:
            content_type = request.headers.get("content-type", "").lower()
            should_check_form = content_type.startswith("application/x-www-form-urlencoded") or content_type.startswith(
                "multipart/form-data"
            )
            if should_check_form:
                try:
                    # BaseHTTPMiddleware consumes the request stream the first time it is read.
                    # Populate the cached body so downstream handlers still receive the payload.
                    await request.body()
                    form = await parse_csrf_form(request)
                except Exception:  # pragma: no cover - fall back to header validation on parse errors
                    form = None
                if form and "_csrf" in form:
                    header_token = form.get("_csrf")

        session = await self._session_manager.load_session(request, allow_inactive=False)
        if not session:
            log_warning("CSRF validation failed - no session", path=path, method=request.method)
            return session_expired_response(request)

        if not header_token:
            log_warning(
                "CSRF validation failed - missing token",
                path=path,
                method=request.method,
                user_id=session.user_id if session else None,
            )
            return JSONResponse(status_code=403, content={"detail": "CSRF token missing"})

        if not secrets.compare_digest(header_token, session.csrf_token):
            log_warning(
                "CSRF validation failed - token mismatch",
                path=path,
                method=request.method,
                user_id=session.user_id if session else None,
            )
            return JSONResponse(status_code=403, content={"detail": "CSRF token mismatch"})

        return await call_next(request)
