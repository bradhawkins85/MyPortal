"""Guards for server-initiated outbound HTTP requests (SSRF defence).

The helpers here resolve a destination hostname and reject addresses that must
never be reachable from a server-side fetch:

* loopback (``127.0.0.0/8``, ``::1``),
* link-local (``169.254.0.0/16``, ``fe80::/10``) which includes cloud metadata
  endpoints such as ``169.254.169.254`` and ``fd00:ec2::254``-style aliases,
* unspecified (``0.0.0.0``, ``::``), multicast and reserved ranges.

RFC1918 / unique-local private ranges are *allowed by default* because
super-admin configured destinations legitimately include LAN services
(ntfy, Uptime Kuma, internal webhooks).  Callers handling less trusted input
can pass ``allow_private=False``.

Use :func:`redirect_guard_hooks` to validate **every** request an
``httpx.AsyncClient`` sends, including each redirect hop, so a public URL cannot
bounce the server to an internal address.  Validation happens at request time,
not only when the URL was saved.

Residual risk: DNS is resolved separately from the connection, so a hostile DNS
server could still rebind between the check and the connect.  This guard is a
defence-in-depth measure, not a network egress policy.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import urlparse

import httpx

__all__ = [
    "UnsafeOutboundURLError",
    "is_forbidden_address",
    "validate_outbound_url",
    "validate_outbound_url_async",
    "redirect_guard_hooks",
]

_METADATA_NETWORKS = (
    # AWS IMDS IPv6 endpoint (fd00:ec2::254) lives in unique-local space.
    ipaddress.ip_network("fd00:ec2::/32"),
    # Alibaba Cloud metadata endpoint.
    ipaddress.ip_network("100.100.100.200/32"),
)


class UnsafeOutboundURLError(ValueError):
    """Raised when an outbound URL targets a forbidden destination."""


def _normalise_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_forbidden_address(ip_value: str | ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool = True) -> bool:
    """Return True when ``ip_value`` must never be fetched by the server."""
    try:
        ip = ipaddress.ip_address(ip_value) if isinstance(ip_value, str) else ip_value
    except ValueError:
        return True
    ip = _normalise_ip(ip)
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or ip.is_reserved
        or any(ip in net for net in _METADATA_NETWORKS if net.version == ip.version)
    ):
        return True
    if not allow_private and (ip.is_private or getattr(ip, "is_shared", False)):
        return True
    return False


def _resolve(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, flags=socket.AI_NUMERICSERV)
    except socket.gaierror as exc:
        raise UnsafeOutboundURLError(f"Unable to resolve hostname: {hostname}") from exc
    return [info[4][0] for info in infos]


def _check_url(url: str, *, allow_private: bool, schemes: Iterable[str]) -> tuple[str, str]:
    parsed = urlparse(str(url).strip())
    if parsed.scheme.lower() not in set(schemes):
        raise UnsafeOutboundURLError("Outbound URL must use http or https")
    hostname = parsed.hostname
    if not hostname:
        raise UnsafeOutboundURLError("Outbound URL must include a hostname")
    return hostname, parsed.scheme


def _check_addresses(hostname: str, addresses: Iterable[str], *, allow_private: bool) -> None:
    for address in addresses:
        # Strip IPv6 scope ids such as "fe80::1%eth0".
        if is_forbidden_address(address.split("%", 1)[0], allow_private=allow_private):
            raise UnsafeOutboundURLError(
                f"Outbound URL host {hostname} resolves to a forbidden address"
            )


def validate_outbound_url(
    url: str,
    *,
    allow_private: bool = True,
    schemes: Iterable[str] = ("http", "https"),
) -> str:
    """Validate ``url`` synchronously; returns the stripped URL or raises."""
    hostname, _ = _check_url(url, allow_private=allow_private, schemes=schemes)
    _check_addresses(hostname, _resolve(hostname), allow_private=allow_private)
    return str(url).strip()


async def validate_outbound_url_async(
    url: str,
    *,
    allow_private: bool = True,
    schemes: Iterable[str] = ("http", "https"),
) -> str:
    """Async variant which resolves DNS off the event loop."""
    hostname, _ = _check_url(url, allow_private=allow_private, schemes=schemes)
    addresses = await asyncio.to_thread(_resolve, hostname)
    _check_addresses(hostname, addresses, allow_private=allow_private)
    return str(url).strip()


def redirect_guard_hooks(
    *,
    allow_private: bool = True,
    extra_check: Callable[[httpx.URL], None] | None = None,
) -> dict[str, list[Callable[[httpx.Request], Awaitable[Any]]]]:
    """Build ``event_hooks`` validating every request (and redirect hop).

    ``extra_check`` may raise ``ValueError`` to add caller-specific rules, such
    as restricting hops to a carrier's own domains.
    """

    async def _guard(request: httpx.Request) -> None:
        if extra_check is not None:
            extra_check(request.url)
        await validate_outbound_url_async(str(request.url), allow_private=allow_private)

    return {"request": [_guard]}
