"""Bounded, SSRF-resistant public website observations."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.repositories import websites as repo

CONNECT_TIMEOUT = 5.0
TOTAL_TIMEOUT = 10.0
MAX_BODY_BYTES = 64 * 1024


async def resolve_public_host(hostname: str) -> list[str]:
    """Resolve *all* addresses and reject special-use destinations.

    Rejecting the complete answer (rather than selecting one acceptable address)
    avoids mixed public/private DNS answers and common rebinding bypasses.
    """
    try:
        answers = await asyncio.get_running_loop().getaddrinfo(
            hostname, None, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise ValueError("Website hostname could not be resolved") from exc
    addresses = sorted({answer[4][0] for answer in answers})
    if not addresses:
        raise ValueError("Website hostname could not be resolved")
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if not address.is_global:
            raise ValueError("Website hostname resolves to a non-public address")
    return addresses


async def validate_public_url(url: str) -> tuple[str, int, list[str]]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP and HTTPS URLs are supported")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not supported")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("Invalid website port") from exc
    if port not in {80, 443}:
        raise ValueError("Only standard web ports are permitted")
    return parsed.hostname.rstrip("."), port, await resolve_public_host(parsed.hostname)


async def inspect_certificate(host: str, port: int, address: str) -> dict[str, Any]:
    if not ipaddress.ip_address(address).is_global:
        raise ValueError("Website connection address is not public")
    context = ssl.create_default_context()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(address, port, ssl=context, server_hostname=host), CONNECT_TIMEOUT
    )
    try:
        cert = writer.get_extra_info("ssl_object").getpeercert()
        expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return {"expires_at": expires.replace(tzinfo=None), "source": "tls-handshake"}
    finally:
        writer.close()
        await writer.wait_closed()


async def lookup_domain_expiry(host: str) -> dict[str, Any] | None:
    """Best-effort expiry from the fixed public RDAP bootstrap service."""
    registrable_candidate = ".".join(host.split(".")[-2:])
    async with httpx.AsyncClient(timeout=TOTAL_TIMEOUT, follow_redirects=False) as client:
        response = await client.get(
            "https://rdap.org/domain/" + registrable_candidate,
            headers={"Accept": "application/rdap+json", "User-Agent": "MyPortal-Monitor/1"},
        )
        if response.status_code != 200:
            return None
        for event in response.json().get("events", []):
            if event.get("eventAction") in {"expiration", "expiry"} and event.get("eventDate"):
                value = datetime.fromisoformat(str(event["eventDate"]).replace("Z", "+00:00"))
                return {"expires_at": value.astimezone(timezone.utc).replace(tzinfo=None),
                        "source": "rdap"}
    return None


def _address_url(url: str, address: str, port: int) -> str:
    """Return *url* with its authority pinned to one validated address."""
    parsed = urlsplit(url)
    address_authority = f"[{address}]" if ":" in address else address
    return urlunsplit((parsed.scheme, f"{address_authority}:{port}",
                       parsed.path, parsed.query, ""))


async def fetch_availability(url: str, host: str, port: int,
                             addresses: list[str]) -> int:
    """Fetch through validated addresses without resolving the hostname again.

    The URL authority controls the actual TCP peer, while Host and sni_hostname
    retain the site's HTTP virtual host and TLS certificate verification name.
    A fresh client per address also prevents a failed attempt from reusing a
    connection associated with another address.
    """
    parsed = urlsplit(url)
    host_header = parsed.netloc
    timeout = httpx.Timeout(TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT)
    last_error: httpx.TransportError | None = None
    for address in addresses:
        # Defense in depth for callers and future retry/address selection changes.
        if not ipaddress.ip_address(address).is_global:
            raise ValueError("Website connection address is not public")
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                async with client.stream(
                    "GET", _address_url(url, address, port),
                    headers={"Host": host_header, "User-Agent": "MyPortal-Monitor/1"},
                    extensions={"sni_hostname": host},
                ) as response:
                    consumed = 0
                    async for chunk in response.aiter_bytes():
                        consumed += len(chunk)
                        if consumed >= MAX_BODY_BYTES:
                            break
                    return response.status_code
        except httpx.TransportError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError("Website hostname could not be resolved")


async def check_website(website: dict[str, Any]) -> dict[str, Any]:
    """Run a check once. Failures are retryable and never erase good observations."""
    checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        host, port, addresses = await validate_public_url(str(website["url"]))
        certificate = None
        if website.get("monitor_tls") and urlsplit(str(website["url"])).scheme == "https":
            certificate = await inspect_certificate(host, port, addresses[0])
        status_code = None
        if website.get("monitor_availability"):
            status_code = await fetch_availability(
                str(website["url"]), host, port, addresses
            )
        dns_facts = {"addresses": addresses} if website.get("collect_dns") else None
        await repo.record_success(int(website["id"]), checked_at, status_code, certificate, dns_facts)
        domain = None
        if website.get("collect_domain_expiry"):
            domain = await lookup_domain_expiry(host)
            if domain:
                await repo.record_domain_expiry(int(website["id"]), checked_at,
                                                domain["expires_at"], domain["source"])
        return {"ok": True, "checked_at": checked_at, "http_status": status_code,
                "certificate": certificate, "dns": dns_facts, "domain": domain}
    except (ValueError, OSError, ssl.SSLError, asyncio.TimeoutError, httpx.HTTPError) as exc:
        message = str(exc) or exc.__class__.__name__
        await repo.record_failure(int(website["id"]), checked_at, message)
        return {"ok": False, "checked_at": checked_at, "error": message, "retryable": True}
