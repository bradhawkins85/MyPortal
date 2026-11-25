"""Restrict staff and configuration pages to trusted networks.

End users have no reason to open staff tools (``/admin/tickets``) or
integration set-up pages (``/xero/connect``, ``/m365/discover``), and those
are the pages attackers probe. When ``TRUSTED_IPS`` is set, requests for the
paths in :data:`DEFAULT_RESTRICTED_PATHS` (plus ``TRUSTED_IP_EXTRA_PATHS``,
minus ``TRUSTED_IP_EXEMPT_PATHS``) are refused unless the client address is
inside one of the trusted networks. When ``TRUSTED_IPS`` is empty every
address is allowed, so existing deployments keep working unchanged.

The client address comes from :func:`app.security.client_ip.get_client_ip`,
which only honours ``X-Forwarded-For`` from ``TRUSTED_PROXIES``.
"""
from __future__ import annotations

import posixpath
import re
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from typing import Awaitable, Callable, Iterable

from fastapi import Request, status
from fastapi.responses import JSONResponse, Response
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.logging import log_error, log_warning
from app.security.client_ip import get_client_ip

Network = IPv4Network | IPv6Network
HtmlRenderer = Callable[[Request], Awaitable[Response]]

#: Path prefixes only staff use from the office. A rule matches the path
#: itself and anything below it; ``*`` matches exactly one path segment.
DEFAULT_RESTRICTED_PATHS: tuple[str, ...] = (
    # Staff work areas and portal administration. ``/admin/profile`` (every
    # user's own profile) and company-admin pages such as ``/admin/marketing``,
    # ``/admin/subscriptions`` and ``/admin/calls`` are deliberately absent.
    "/admin/tickets",
    "/admin/approvals",
    "/admin/companies",
    "/admin/users",
    "/admin/roles",
    "/admin/sessions",
    "/admin/impersonation",
    "/admin/anonymisation",
    "/admin/audit-logs",
    "/admin/change-log",
    "/admin/automation",
    "/admin/scheduled-tasks",
    "/admin/cron-calendar",
    "/admin/system-updates",
    "/admin/env-checker",
    "/admin/feature-packs",
    "/admin/modules",
    "/admin/webhooks",
    "/admin/tray",
    "/admin/csp",
    "/admin/service-status",
    "/admin/custom-forms",
    "/admin/message-templates",
    "/admin/knowledge-base",
    "/admin/business-continuity-plans",
    "/admin/compliance-checks",
    "/admin/dmarc",
    "/admin/chat",
    "/admin/call-recordings",
    "/admin/syncro",
    "/admin/tags",
    "/admin/tag-exclusions",
    "/admin/tag-synonyms",
    "/admin/ai-quality",
    "/admin/rag",
    "/admin/llm-usage",
    "/admin/benchmarking",
    # Xero connection set-up (OAuth connect and its callback).
    "/xero",
    # Microsoft 365 connection set-up. Mailbox pages stay available, and
    # ``/m365/callback`` is shared with Microsoft sign-in so it stays open.
    "/m365/discover",
    "/m365/provision",
    "/m365/connect",
    "/m365/connection",
    "/m365/credentials",
    "/m365/admin-credentials",
    "/m365/test",
    "/m365/diagnostics",
    "/m365/permissions",
    "/m365/best-practices/settings",
    "/m365/best-practices/remediate",
    "/m365/best-practices/remediate-batch",
    "/companies/*/m365-credentials",
    # Administration APIs and the API reference.
    "/docs",
    "/internal/openapi.json",
    "/api-keys",
    "/audit-logs",
    "/scheduler",
    "/api/system",
    "/api/features",
    "/api/plugins",
)

BLOCKED_DETAIL = "This page is only available from a trusted network."


def _split_entries(raw: str | Iterable[str]) -> list[str]:
    """Split a setting into entries, forgiving how ``.env`` files get written.

    Values arrive verbatim from systemd and Docker env files, so an inline
    ``# comment``, wrapping quotes, or spaces/semicolons between entries must
    not turn valid addresses into invalid ones.
    """

    text = raw if isinstance(raw, str) else ",".join(str(item) for item in raw)
    text = text.split("#", 1)[0]
    return [part.strip("\"'") for part in re.split(r"[,;\s]+", text) if part.strip("\"'")]


def parse_networks(raw: str | Iterable[str] | None) -> tuple[list[Network], list[str]]:
    """Parse IPs/CIDR ranges into networks plus the entries that are invalid."""

    if raw is None:
        return [], []
    networks: list[Network] = []
    invalid: list[str] = []
    for text in _split_entries(raw):
        try:
            networks.append(ip_network(text, strict=False))
        except ValueError:
            invalid.append(text)
    return networks, invalid


def parse_paths(raw: str | Iterable[str] | None) -> list[str]:
    """Normalise a comma-separated list of path rules."""

    if raw is None:
        return []
    rules: list[str] = []
    for text in _split_entries(raw):
        rules.append(normalise_path(text if text.startswith("/") else "/" + text))
    return rules


def normalise_path(path: str) -> str:
    """Collapse duplicate slashes and dot segments so rules cannot be sidestepped."""

    if not path:
        return "/"
    normalised = posixpath.normpath("/" + path.lstrip("/"))
    # ``normpath`` keeps a leading ``//``; collapse it like the rest.
    return "/" + normalised.lstrip("/")


def path_matches(path: str, rule: str) -> bool:
    """Return True when ``path`` is ``rule`` or below it (``*`` = one segment)."""

    path_parts = [part for part in path.split("/") if part]
    rule_parts = [part for part in rule.split("/") if part]
    if len(path_parts) < len(rule_parts):
        return False
    return all(r == "*" or r == p for r, p in zip(rule_parts, path_parts))


class TrustedNetworkPolicy:
    """Decide whether a request path needs a trusted client address."""

    def __init__(
        self,
        *,
        trusted_ips: str | Iterable[str] | None,
        extra_paths: str | Iterable[str] | None = None,
        exempt_paths: str | Iterable[str] | None = None,
        restricted_paths: Iterable[str] = DEFAULT_RESTRICTED_PATHS,
    ) -> None:
        self.networks, self.invalid_entries = parse_networks(trusted_ips)
        self.restricted_paths = tuple(
            dict.fromkeys([*parse_paths(restricted_paths), *parse_paths(extra_paths)])
        )
        self.exempt_paths = tuple(parse_paths(exempt_paths))

    @property
    def configured(self) -> bool:
        """True when ``TRUSTED_IPS`` held any entry, valid or not."""

        return bool(self.networks or self.invalid_entries)

    def is_restricted(self, path: str) -> bool:
        if not self.configured:
            return False
        normalised = normalise_path(path)
        if any(path_matches(normalised, rule) for rule in self.exempt_paths):
            return False
        return any(path_matches(normalised, rule) for rule in self.restricted_paths)

    def is_trusted_ip(self, client_ip: str | None) -> bool:
        if not self.configured:
            return True
        if not client_ip:
            return False
        try:
            address = ip_address(client_ip)
        except ValueError:
            return False
        # An IPv4 client reached over an IPv6 socket arrives as ::ffff:a.b.c.d.
        mapped = getattr(address, "ipv4_mapped", None)
        candidates = [address, mapped] if mapped else [address]
        return any(candidate in network for candidate in candidates for network in self.networks)


class TrustedNetworkMiddleware:
    """Refuse restricted paths to clients outside ``TRUSTED_IPS``.

    Records ``request.state.trusted_network`` for every request so templates
    can hide set-up actions that would be refused.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        policy: TrustedNetworkPolicy,
        html_renderer: HtmlRenderer | None = None,
    ) -> None:
        self.app = app
        self.policy = policy
        self.html_renderer = html_renderer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        # Request() asserts scope["type"] == "http"; HTTPConnection also
        # accepts websocket scopes and exposes the client and headers needed.
        connection = HTTPConnection(scope)
        client_ip = get_client_ip(connection, default=None) if self.policy.configured else None
        trusted = self.policy.is_trusted_ip(client_ip)
        state = scope.setdefault("state", {})
        state["trusted_network"] = trusted
        state["trusted_network_client_ip"] = client_ip

        path = scope.get("path") or "/"
        if trusted or not self.policy.is_restricted(path):
            await self.app(scope, receive, send)
            return

        log_warning(
            "Trusted network check refused request; add the client address to TRUSTED_IPS if it should be allowed",
            client_ip=client_ip or "unknown",
            peer_ip=(scope.get("client") or ("unknown",))[0],
            path=path,
            method=scope.get("method", "WEBSOCKET"),
        )
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = await self._blocked_response(Request(scope, receive))
        await response(scope, receive, send)

    async def _blocked_response(self, request: Request) -> Response:
        accept = request.headers.get("accept", "")
        wants_html = request.method in {"GET", "HEAD"} and "text/html" in accept
        if wants_html and self.html_renderer is not None:
            try:
                return await self.html_renderer(request)
            except Exception as exc:  # pragma: no cover - fall back to JSON
                log_error("Failed to render trusted network error page", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": BLOCKED_DETAIL, "client_ip": client_ip_seen(request)},
        )


def is_trusted_network(request: Request) -> bool:
    """Whether the request came from a trusted network (True when unrestricted)."""

    return bool(getattr(request.state, "trusted_network", True))


def client_ip_seen(request: Request) -> str | None:
    """The address the trusted network check judged, for refusal messages."""

    return getattr(request.state, "trusted_network_client_ip", None)
