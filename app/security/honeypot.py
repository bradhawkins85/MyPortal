"""Honeypot detection for bots scanning the portal.

Legitimate traffic only reaches MyPortal through links rendered by the app
itself or through the Tray and RMM agents, so a request for a path that no
route serves is a strong scanning signal. This middleware lets every request
run normally and, once the response is known, classifies requests that ended
in an error on a path the application does not serve:

* ``HONEYPOT TRAP`` - the path matches a well-known scanner signature
  (``/wp-login.php``, ``/.env``, ``/.git/config``, ``/phpmyadmin`` ...). These
  are never legitimate for a Python portal, so Fail2ban bans on the first hit.
* ``HONEYPOT PROBE`` - any other unknown endpoint, including unknown ``/api``
  paths. Fail2ban bans once an address racks up several probes in a window.

Nothing is reported for paths a route serves (even when the handler returns
404 for a missing record), for signed-in browser sessions, for agent API
prefixes, or for files browsers request automatically. Lines are written via
loguru so they reach the ``FAIL2BAN_LOG_PATH`` sink, where the bundled
``myportal-honeypot`` filter turns them into iptables bans.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from loguru import logger
from starlette.requests import Request
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.security.client_ip import get_client_ip

# Agents talk to these prefixes. A newer agent calling an endpoint an older
# portal lacks must never get the customer's office IP banned.
DEFAULT_PROBE_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/tray/",
    "/api/rmm/",
    "/api/agent/",
    "/api/health-checks/",
    "/static/",
    "/.well-known/",
)

# Files browsers, crawlers and OS integrations fetch without a link.
DEFAULT_PROBE_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/favicon.ico",
        "/robots.txt",
        "/sitemap.xml",
        "/browserconfig.xml",
        "/site.webmanifest",
        "/manifest.json",
        "/apple-touch-icon.png",
        "/apple-touch-icon-precomposed.png",
        "/apple-touch-icon-120x120.png",
        "/apple-touch-icon-120x120-precomposed.png",
        "/apple-touch-icon-152x152.png",
        "/apple-touch-icon-152x152-precomposed.png",
    }
)

# Status codes produced before routing for reasons unrelated to scanning.
_IGNORED_STATUSES = frozenset({429, 503})

_TRAP_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "script_extension",
        re.compile(
            r"\.(?:php\d?|phtml|phar|asp|aspx|ashx|asmx|axd|jsp|jspx|cgi|pl|cfm|shtml)(?:/|$)"
        ),
    ),
    (
        "dotfile",
        re.compile(
            r"(?:^|/)\.(?:env|git|svn|hg|bzr|aws|ssh|docker|kube|vscode|idea|ds_store|"
            r"htaccess|htpasswd|npmrc|bash_history|config)(?:/|\.|$)"
        ),
    ),
    (
        "known_scanner_target",
        re.compile(
            r"(?:^|/)(?:wp-admin|wp-login|wp-content|wp-includes|wp-json|wordpress|xmlrpc|"
            r"phpmyadmin|phpmyadmin\d*|pma|myadmin|mysqladmin|adminer|cgi-bin|"
            r"vendor/phpunit|boaform|hnap1|_ignition|actuator|server-status|server-info|"
            r"jmx-console|manager/html|solr/admin|owa/auth|ecp/default|remote/login|"
            r"geoserver|webui|cf_scripts|setup\.cgi|shell|cmd)(?:/|\.|$)"
        ),
    ),
    (
        "backup_archive",
        re.compile(
            r"\.(?:sql|sql\.gz|bak|old|orig|swp|save|backup|tar|tar\.gz|tgz|7z|rar)$"
        ),
    ),
    ("path_traversal", re.compile(r"(?:\.\./|\.\.\\|%2e%2e|/etc/passwd|win\.ini)")),
)

_UNSAFE_LOG_CHARS = re.compile(r"[^\x21-\x7e]")
_MAX_LOGGED_PATH = 200


def match_trap_rule(path: str, raw_path: str = "") -> str | None:
    """Return the name of the scanner signature ``path`` matches, if any."""

    candidates = [path.lower()]
    if raw_path:
        candidates.append(raw_path.lower())
    for name, pattern in _TRAP_RULES:
        if any(pattern.search(candidate) for candidate in candidates):
            return name
    return None


def _safe_for_log(value: str) -> str:
    """Make attacker-controlled text safe to embed in a single log token."""

    cleaned = _UNSAFE_LOG_CHARS.sub("_", value).replace("|", "_")
    if len(cleaned) > _MAX_LOGGED_PATH:
        cleaned = cleaned[:_MAX_LOGGED_PATH] + "..."
    return cleaned or "-"


def _path_is_routed(app: Any, scope: Scope) -> bool:
    """Return True when any route or mount on ``app`` serves ``scope['path']``."""

    router = getattr(app, "router", None)
    routes = getattr(router, "routes", None)
    if not routes:
        # Without a router to consult, never accuse the caller of probing.
        return True
    for scope_type in ("http", "websocket"):
        probe_scope = dict(scope)
        probe_scope["type"] = scope_type
        for route in list(routes):
            try:
                match, _ = route.matches(probe_scope)
            except Exception:  # pragma: no cover - defensive against odd routes
                continue
            if match != Match.NONE:
                return True
    return False


class HoneypotMiddleware:
    """Report requests for unknown paths so Fail2ban can block scanners."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool = True,
        probe_exempt_prefixes: Iterable[str] = DEFAULT_PROBE_EXEMPT_PREFIXES,
        probe_exempt_paths: Iterable[str] = DEFAULT_PROBE_EXEMPT_PATHS,
        session_loader: Any = None,
    ) -> None:
        self.app = app
        self._enabled = enabled
        self._probe_exempt_prefixes = tuple(probe_exempt_prefixes)
        self._probe_exempt_paths = frozenset(probe_exempt_paths)
        self._session_loader = session_loader

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status_holder: dict[str, int] = {}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message.get("status", 0))
            await send(message)

        await self.app(scope, receive, send_wrapper)

        status_code = status_holder.get("status", 0)
        if status_code < 400 or status_code in _IGNORED_STATUSES:
            return
        if "route" in scope or "endpoint" in scope:
            return
        try:
            await self._classify(scope, status_code)
        except Exception as exc:  # pragma: no cover - never break a response
            logger.debug(f"Honeypot classification failed error={type(exc).__name__}")

    async def _classify(self, scope: Scope, status_code: int) -> None:
        if _path_is_routed(scope.get("app"), scope):
            return

        path = str(scope.get("path") or "/")
        raw_path_bytes = scope.get("raw_path") or b""
        raw_path = raw_path_bytes.decode("latin-1") if isinstance(raw_path_bytes, bytes) else ""
        rule = match_trap_rule(path, raw_path)
        if rule is None and self._is_probe_exempt(path):
            return

        request = Request(scope)
        if await self._has_valid_session(request):
            return

        ip = get_client_ip(request, default=None)
        if not ip:
            return
        method = _safe_for_log(str(scope.get("method") or "-"))
        safe_path = _safe_for_log(path)
        # ``ip`` must stay the first field: the Fail2ban filter anchors on it,
        # so text a scanner puts in the path can never name another address.
        if rule is not None:
            message = (
                f"HONEYPOT TRAP ip={ip} rule={rule} status={status_code} "
                f"method={method} path={safe_path}"
            )
        else:
            message = (
                f"HONEYPOT PROBE ip={ip} status={status_code} "
                f"method={method} path={safe_path}"
            )
        logger.bind(request_id="-", user_id="-", feature="honeypot").warning(message)

    def _is_probe_exempt(self, path: str) -> bool:
        if path in self._probe_exempt_paths:
            return True
        return path.startswith(self._probe_exempt_prefixes)

    async def _has_valid_session(self, request: Request) -> bool:
        loader = self._session_loader
        if loader is None:
            from app.security.session import session_manager

            loader = session_manager.load_session
        if not request.cookies:
            return False
        try:
            return await loader(request) is not None
        except Exception:  # pragma: no cover - treat lookup failures as anonymous
            return False
