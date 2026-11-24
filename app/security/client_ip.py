"""Helpers for determining the real client IP in a proxy-aware way.

Several parts of the application (rate limiters, audit logging, session
creation, CSRF) need to know the *real* client IP address. Historically they
read ``X-Forwarded-For`` directly, which is trivially spoofable when the
application is reachable without a reverse proxy in front of it.

The functions in this module centralise the decision and honour the
``TRUSTED_PROXIES`` setting: only when the direct peer is one of the
configured trusted proxies will ``X-Forwarded-For`` / ``X-Real-IP`` be
consulted. Otherwise the direct socket peer address is used.
"""
from __future__ import annotations

from ipaddress import ip_address
from typing import Any

from fastapi import Request

from app.core.config import get_settings


def _peer_is_trusted(peer_ip: str, trusted_networks: list[Any]) -> bool:
    if not trusted_networks:
        return False
    try:
        addr = ip_address(peer_ip)
    except (ValueError, TypeError):
        return False
    for network in trusted_networks:
        try:
            if addr in network:  # type: ignore[operator]
                return True
        except TypeError:
            continue
    return False


def _client_from_forwarded_for(request: Request, trusted: list[Any]) -> str | None:
    """Return the address the outermost trusted proxy received the request from.

    Each proxy appends the address it saw to ``X-Forwarded-For``, so only the
    right-hand entries were written by trusted proxies; anything further left
    came from the client and can be forged. Walk from the right, skipping
    trusted proxies, and return the first other address. When every entry is
    a trusted proxy, the left-most one is the closest to the client.
    """

    getter = getattr(request.headers, "getlist", None)
    values = getter("x-forwarded-for") if callable(getter) else [request.headers.get("x-forwarded-for")]
    entries = [part.strip() for value in values if value for part in value.split(",")]
    entries = [entry for entry in entries if entry]
    if not entries:
        return None
    for entry in reversed(entries):
        try:
            ip_address(entry)
        except ValueError:
            # Unparseable text cannot be a hop a trusted proxy recorded.
            return None
        if not _peer_is_trusted(entry, trusted):
            return entry
    return entries[0]


def get_client_ip(
    request: Request,
    *,
    default: str | None = "anonymous",
    trusted_networks: list[Any] | None = None,
) -> str | None:
    """Return the best-effort client IP for ``request``.

    Behaviour:

    * If the direct peer (``request.client.host``) is inside one of the
      configured ``TRUSTED_PROXIES`` networks, the right-most
      ``X-Forwarded-For`` entry that is not itself a trusted proxy is
      returned (falling back to ``X-Real-IP``). Entries further left are
      written by the client and are never trusted.
    * Otherwise the direct peer is returned, regardless of any proxy
      headers the attacker may have sent.
    * If neither is available, ``default`` is returned (defaults to
      ``"anonymous"`` to keep rate-limit buckets deterministic).

    ``trusted_networks`` can be supplied directly (for testing) to bypass
    the global settings lookup.
    """

    if trusted_networks is None:
        settings = get_settings()
        trusted = settings.trusted_proxy_networks()
    else:
        trusted = trusted_networks
    peer_ip = request.client.host if request.client else None

    if peer_ip and _peer_is_trusted(peer_ip, trusted):
        forwarded = _client_from_forwarded_for(request, trusted)
        if forwarded:
            return forwarded
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            real_ip = real_ip.strip()
            if real_ip:
                return real_ip

    if peer_ip:
        return peer_ip
    return default
