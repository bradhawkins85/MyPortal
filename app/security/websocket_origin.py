"""Cross-site WebSocket hijacking protection for cookie-authenticated sockets."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.security.client_ip import _peer_is_trusted


def bearer_token(websocket: Any) -> str:
    """The bearer token from a WebSocket handshake's Authorization header."""

    header = websocket.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    return ""


def origin_allowed(websocket: Any, settings: Any) -> bool:
    """Whether a browser WebSocket handshake comes from this portal.

    A handshake without an ``Origin`` header (not sent by a browser) is
    allowed; one with an ``Origin`` must match the request host or the
    configured portal URL. ``X-Forwarded-Host`` only counts from configured
    trusted proxies, mirroring the X-Forwarded-For handling in
    :func:`app.security.client_ip.get_client_ip`; otherwise an
    attacker-controlled header would whitelist any Origin host.
    """

    origin = (websocket.headers.get("origin") or "").strip()
    if not origin:
        return True
    origin_host = urlsplit(origin).netloc.lower()
    allowed_hosts = {(websocket.headers.get("host") or "").strip().lower()}
    peer_ip = websocket.client.host if websocket.client else None
    if peer_ip and _peer_is_trusted(peer_ip, settings.trusted_proxy_networks()):
        allowed_hosts.add((websocket.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower())
    if settings.portal_url:
        allowed_hosts.add(urlsplit(settings.portal_url.unicode_string()).netloc.lower())
    allowed_hosts.discard("")
    return bool(origin_host) and origin_host in allowed_hosts
