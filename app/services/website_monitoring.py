"""Bounded, SSRF-resistant website, TLS, registration and DNS observations."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import ssl
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import dns.asyncresolver
import dns.exception
import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from publicsuffix2 import get_sld

from app.repositories import websites as repo

CONNECT_TIMEOUT = 5.0
TOTAL_TIMEOUT = 10.0
MAX_BODY_BYTES = 64 * 1024
MAX_RDAP_BYTES = 256 * 1024
DNS_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "NS", "SOA", "CAA", "SRV", "DNSKEY", "DS")


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


async def resolve_public_host(hostname: str) -> list[str]:
    try:
        answers = await asyncio.get_running_loop().getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Website hostname could not be resolved") from exc
    addresses = sorted({answer[4][0] for answer in answers})
    if not addresses:
        raise ValueError("Website hostname could not be resolved")
    if any(not ipaddress.ip_address(raw).is_global for raw in addresses):
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
    host = parsed.hostname.rstrip(".").lower()
    return host, port, await resolve_public_host(host)


def _certificate_facts(der: bytes, host: str, *, trusted: bool) -> dict[str, Any]:
    cert = x509.load_der_x509_certificate(der)
    try:
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        sans = []
    names = sans or [attribute.value for attribute in cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)]
    try:
        ssl.match_hostname({"subjectAltName": [("DNS", name) for name in names]}, host)
        hostname_match = True
    except (ssl.CertificateError, ValueError):
        hostname_match = False
    now = datetime.now(timezone.utc)
    expires = cert.not_valid_after_utc
    status = "expired" if expires <= now else "hostname_mismatch" if not hostname_match else "valid" if trusted else "untrusted"
    return {
        "status": status, "hostname_match": hostname_match, "trusted": trusted,
        "subject": cert.subject.rfc4514_string(), "issuer": cert.issuer.rfc4514_string(),
        "serial": format(cert.serial_number, "X"), "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(),
        "sans": sorted(names), "valid_from": _utc_naive(cert.not_valid_before_utc).isoformat(),
        "expires_at": _utc_naive(expires), "source": "tls-handshake",
    }


async def _tls_der(host: str, port: int, address: str, context: ssl.SSLContext) -> bytes:
    if not ipaddress.ip_address(address).is_global:
        raise ValueError("Website connection address is not public")
    _reader, writer = await asyncio.wait_for(asyncio.open_connection(address, port, ssl=context, server_hostname=host), CONNECT_TIMEOUT)
    try:
        return writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    finally:
        writer.close()
        await writer.wait_closed()


async def inspect_certificate(host: str, port: int, address: str) -> dict[str, Any]:
    try:
        der = await _tls_der(host, port, address, ssl.create_default_context())
        return _certificate_facts(der, host, trusted=True)
    except ssl.SSLCertVerificationError:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        der = await _tls_der(host, port, address, context)
        return _certificate_facts(der, host, trusted=False)


def registrable_domain(host: str) -> str:
    domain = get_sld(host.lower().rstrip("."), strict=True)
    if not domain:
        raise ValueError("No registrable domain could be determined")
    return domain


async def lookup_registration(host: str) -> dict[str, Any]:
    domain = registrable_domain(host)
    url = "https://rdap.org/domain/" + domain
    async with httpx.AsyncClient(timeout=TOTAL_TIMEOUT, follow_redirects=False) as client:
        for _ in range(4):
            # RDAP bootstrap redirects to the responsible registry. Validate each
            # destination before allowing the HTTP client to resolve it itself.
            await validate_public_url(url)
            response = await client.get(url, headers={"Accept": "application/rdap+json", "User-Agent": "MyPortal-Monitor/1"})
            if not response.is_redirect:
                break
            location = response.headers.get("location")
            if not location or not location.startswith("https://"):
                raise ValueError("RDAP returned an unsafe redirect")
            url = location
        else:
            raise ValueError("RDAP returned too many redirects")
        if response.status_code in {404, 410}:
            return {"status": "unavailable", "domain": domain, "source": "rdap"}
        response.raise_for_status()
        if len(response.content) > MAX_RDAP_BYTES:
            raise ValueError("RDAP response exceeded the size limit")
        payload = response.json()
    events = {str(e.get("eventAction")): e.get("eventDate") for e in payload.get("events", []) if e.get("eventDate")}
    registrar = next((e for e in payload.get("entities", []) if "registrar" in e.get("roles", [])), None)
    registrar_name = None
    if registrar:
        vcard = registrar.get("vcardArray", [None, []])
        registrar_name = next((item[3] for item in vcard[1] if item and item[0] == "fn"), None)
    return {"status": "available", "domain": domain, "source": "rdap", "registrar": registrar_name or "Redacted/unavailable",
            "registered_at": events.get("registration"), "updated_at": events.get("last changed") or events.get("last update of RDAP database"),
            "expires_at": events.get("expiration") or events.get("expiry"), "statuses": sorted(payload.get("status", [])),
            "nameservers": sorted(n.get("ldhName", "").lower() for n in payload.get("nameservers", []) if n.get("ldhName"))}


async def lookup_domain_expiry(host: str) -> dict[str, Any] | None:
    registration = await lookup_registration(host)
    value = registration.get("expires_at")
    if value:
        expires = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        registration["expires_at"] = _utc_naive(expires)
    return registration


async def lookup_dns(host: str) -> dict[str, Any]:
    apex = registrable_domain(host)
    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = CONNECT_TIMEOUT
    resolver.lifetime = TOTAL_TIMEOUT
    records = []
    for name in dict.fromkeys((host, apex)):
        for record_type in DNS_TYPES:
            try:
                answer = await resolver.resolve(name, record_type, raise_on_no_answer=False, search=False)
            except (dns.exception.DNSException, OSError):
                continue
            values = sorted({r.to_text().strip() for r in answer})
            if values:
                records.append({"name": name.lower() + ".", "type": record_type, "values": values})
    return {"source": "recursive-dns", "coverage": "public lookup", "records": sorted(records, key=lambda r: (r["name"], r["type"]))}


def _address_url(url: str, address: str, port: int) -> str:
    parsed = urlsplit(url); authority = f"[{address}]" if ":" in address else address
    return urlunsplit((parsed.scheme, f"{authority}:{port}", parsed.path, parsed.query, ""))


async def fetch_availability(url: str, host: str, port: int, addresses: list[str]) -> int:
    parsed = urlsplit(url); timeout = httpx.Timeout(TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT); last_error = None
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise ValueError("Website connection address is not public")
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                async with client.stream("GET", _address_url(url, address, port), headers={"Host": parsed.netloc, "User-Agent": "MyPortal-Monitor/1"}, extensions={"sni_hostname": host}) as response:
                    consumed = 0
                    async for chunk in response.aiter_bytes():
                        consumed += len(chunk)
                        if consumed >= MAX_BODY_BYTES: break
                    return response.status_code
        except httpx.TransportError as exc: last_error = exc
    if last_error: raise last_error
    raise ValueError("Website hostname could not be resolved")


async def check_website(website: dict[str, Any]) -> dict[str, Any]:
    """Run identical manual/scheduled observations; preserve good data on component failure."""
    checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        host, port, addresses = await validate_public_url(str(website["url"]))
    except (ValueError, OSError, asyncio.TimeoutError) as exc:
        await repo.record_failure(int(website["id"]), checked_at, str(exc) or exc.__class__.__name__)
        return {"ok": False, "checked_at": checked_at, "error": str(exc), "retryable": True}
    certificate = registration = dns_facts = None
    errors = []
    if website.get("monitor_tls") and urlsplit(str(website["url"])).scheme == "https":
        try: certificate = await inspect_certificate(host, port, addresses[0])
        except (ValueError, OSError, ssl.SSLError, asyncio.TimeoutError) as exc:
            errors.append("TLS: " + (str(exc) or exc.__class__.__name__))
            await repo.record_component_failure(int(website["id"]), "certificate", checked_at, errors[-1])
    if website.get("collect_domain_expiry"):
        try:
            registration = await lookup_domain_expiry(host)
            if registration and registration.get("expires_at"):
                await repo.record_domain_expiry(int(website["id"]), checked_at,
                                                registration["expires_at"], registration["source"])
        except (ValueError, OSError, httpx.HTTPError, asyncio.TimeoutError) as exc:
            errors.append("Registration: " + (str(exc) or exc.__class__.__name__))
            await repo.record_component_failure(int(website["id"]), "registration", checked_at, errors[-1])
    if website.get("collect_dns"):
        try: dns_facts = await lookup_dns(host)
        except (ValueError, OSError, asyncio.TimeoutError) as exc:
            errors.append("DNS: " + (str(exc) or exc.__class__.__name__))
            await repo.record_component_failure(int(website["id"]), "dns", checked_at, errors[-1])
    try:
        status_code = await fetch_availability(str(website["url"]), host, port, addresses) if website.get("monitor_availability") else None
    except (ValueError, OSError, ssl.SSLError, asyncio.TimeoutError, httpx.HTTPError) as exc:
        await repo.record_failure(int(website["id"]), checked_at, str(exc) or exc.__class__.__name__)
        return {"ok": False, "checked_at": checked_at, "error": str(exc), "retryable": True}
    await repo.record_success(int(website["id"]), checked_at, status_code, certificate, dns_facts, registration)
    return {"ok": True, "checked_at": checked_at, "http_status": status_code, "certificate": certificate, "dns": dns_facts, "domain": registration, "warnings": errors}


async def check_dns(website: dict[str, Any]) -> dict[str, Any]:
    """Collect DNS only; never make an HTTP, TLS, or registration request."""
    checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if not website.get("collect_dns"):
        return {"ok": True, "checked_at": checked_at, "skipped": True}
    try:
        parsed = urlsplit(str(website["url"]))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Only absolute HTTP and HTTPS URLs are supported")
        dns_facts = await lookup_dns(parsed.hostname.rstrip(".").lower())
        await repo.record_dns_success(int(website["id"]), checked_at, dns_facts)
        return {"ok": True, "checked_at": checked_at, "dns": dns_facts}
    except (ValueError, OSError, asyncio.TimeoutError) as exc:
        message = "DNS: " + (str(exc) or exc.__class__.__name__)
        await repo.record_component_failure(int(website["id"]), "dns", checked_at, message)
        return {"ok": False, "checked_at": checked_at, "error": message, "retryable": True}
