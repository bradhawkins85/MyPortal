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


def get_client_ip(
    request: Request,
    *,
    default: str | None = "anonymous",
    trusted_networks: list[Any] | None = None,
) -> str | None:
    """Return the best-effort client IP for ``request``.

    Behaviour:

    * If the direct peer (``request.client.host``) is inside one of the
      configured ``TRUSTED_PROXIES`` networks, the first value from
      ``X-Forwarded-For`` (falling back to ``X-Real-IP``) is returned.
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
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            real_ip = real_ip.strip()
            if real_ip:
                return real_ip

    if peer_ip:
        return peer_ip
    return default
