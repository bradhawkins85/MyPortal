"""Look up asset warranty dates from each manufacturer's own free warranty service.

Only interfaces published or used by the vendor's own website are called:

* Dell — the TechDirect Warranty API (``apigtwb2c.us.dell.com``). Free, but
  each organisation registers at https://tdm.dell.com for a client ID/secret.
* HP — the HP Developer Portal Warranty API (``warranty.api.hp.com``). Free,
  with an API key/secret from https://developers.hp.com.
* Lenovo — the warranty lookup behind Lenovo's own support site
  (``pcsupport.lenovo.com``), which needs no key. When Lenovo has issued a
  Warranty API ClientID, the official ``supportapi.lenovo.com`` is used instead.

ASUS, MSI, Gigabyte, Intel, Apple, Microsoft and Acer publish no free automated
warranty interface (their checks sit behind CAPTCHAs or sign-in), so assets
from those vendors are marked ``unsupported`` with a link to the vendor's own
check page, and technicians record the dates by hand.

Credentials come from ``WARRANTY_LOOKUP_*`` environment variables and the
service is gated by the ``warranty-lookup`` module toggle. Only the serial
number (and nothing else about the asset) is sent, and only to its own vendor.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

import httpx

from app.core.config import get_settings
from app.core.logging import log_error, log_info
from app.repositories import assets as asset_repo
from app.services import modules as modules_service
from app.services.monitored_http import monitored_client

MODULE_SLUG = "warranty-lookup"
REQUEST_TIMEOUT = 20.0
# Vendors throttle aggressively; keep well under one request per second.
_REQUEST_INTERVAL_SECONDS = 1.5
_request_lock = asyncio.Lock()
_USER_AGENT = "MyPortal-WarrantyLookup/1.0"

DELL_AUTH_URL = "https://apigtwb2c.us.dell.com/auth/oauth/v2/token"
DELL_ENTITLEMENTS_URL = "https://apigtwb2c.us.dell.com/PROD/sbil/eapi/v5/asset-entitlements"
HP_AUTH_URL = "https://warranty.api.hp.com/oauth/v1/token"
HP_QUERY_URL = "https://warranty.api.hp.com/productwarranty/v2/queries"
LENOVO_SUPPORT_SITE_URL = "https://pcsupport.lenovo.com/us/en/api/v4/upsell/redport/getIbaseInfo"
LENOVO_API_URL = "https://supportapi.lenovo.com/v2.5/warranty"

SUPPORTED_VENDORS = ("dell", "hp", "lenovo")
VENDOR_LABELS = {
    "dell": "Dell", "hp": "HP", "lenovo": "Lenovo", "asus": "ASUS", "msi": "MSI",
    "gigabyte": "Gigabyte", "intel": "Intel", "apple": "Apple", "microsoft": "Microsoft",
    "acer": "Acer", "hpe": "HPE", "manual": "Entered manually",
}
# Each vendor's own warranty or support page, for vendors without a free API.
VENDOR_CHECK_URLS = {
    "asus": "https://www.asus.com/support/warranty-status-inquiry/",
    "msi": "https://www.msi.com/support",
    "gigabyte": "https://www.gigabyte.com/Support",
    "intel": "https://www.intel.com/content/www/us/en/support.html",
    "apple": "https://checkcoverage.apple.com/",
    "microsoft": "https://account.microsoft.com/devices",
    "acer": "https://www.acer.com/us-en/support",
    "hpe": "https://support.hpe.com/",
}
# Ordered: the first pattern found in the manufacturer/model text wins.
_VENDOR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hpe", re.compile(r"hewlett packard enterprise|\bhpe\b")),
    ("dell", re.compile(r"\bdell\b|alienware")),
    ("hp", re.compile(r"hewlett[- ]?packard|\bhp\b")),
    ("lenovo", re.compile(r"lenovo")),
    ("asus", re.compile(r"\basus|asustek")),
    ("msi", re.compile(r"micro-star|\bmsi\b")),
    ("gigabyte", re.compile(r"gigabyte")),
    ("intel", re.compile(r"\bintel\b")),
    ("apple", re.compile(r"\bapple\b|\bmacbook|\bimac\b|\bmac ?mini\b")),
    ("microsoft", re.compile(r"microsoft|\bsurface\b")),
    ("acer", re.compile(r"\bacer\b")),
)
_SERIAL_RE = re.compile(r"^[A-Za-z0-9-]{4,30}$")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


class WarrantyLookupDisabledError(RuntimeError):
    """Raised when the warranty lookup module is switched off."""


@dataclass(frozen=True)
class WarrantyResult:
    status: str  # found, not_found, unsupported, not_configured, error
    vendor: str | None
    message: str
    start_date: date | None = None
    end_date: date | None = None


# ---------------------------------------------------------------------------
# Vendor detection and response parsing
# ---------------------------------------------------------------------------


def detect_vendor(*values: Any) -> str | None:
    """Return the vendor key for the first manufacturer/model text that names one."""
    for value in values:
        text = str(value or "").strip().casefold()
        if not text:
            continue
        for vendor, pattern in _VENDOR_PATTERNS:
            if pattern.search(text):
                return vendor
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    match = _DATE_RE.match(str(value or "").strip())
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _coverage(
    periods: Iterable[Any], start_keys: tuple[str, ...], end_keys: tuple[str, ...]
) -> tuple[date | None, date | None, str | None]:
    """Return (earliest start, latest end, description of the latest-ending period)."""
    start: date | None = None
    end: date | None = None
    description: str | None = None
    for period in periods:
        if not isinstance(period, dict):
            continue
        period_start = next((d for d in (_parse_date(period.get(k)) for k in start_keys) if d), None)
        period_end = next((d for d in (_parse_date(period.get(k)) for k in end_keys) if d), None)
        if period_start and (start is None or period_start < start):
            start = period_start
        if period_end and (end is None or period_end > end):
            end = period_end
            description = next(
                (str(period[k]).strip() for k in (
                    "serviceLevelDescription", "offerDescription", "name", "Name", "description",
                ) if period.get(k)),
                None,
            )
    return start, end, description


def _found(vendor: str, start: date | None, end: date | None, description: str | None) -> WarrantyResult:
    if not end:
        return WarrantyResult("not_found", vendor, f"{VENDOR_LABELS[vendor]} returned no warranty dates.")
    detail = f" ({description})" if description else ""
    return WarrantyResult(
        "found", vendor, f"{VENDOR_LABELS[vendor]} warranty{detail} until {end.isoformat()}.", start, end,
    )


def parse_dell_entitlements(payload: Any, serial: str) -> WarrantyResult:
    records = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
    record = next(
        (r for r in records if isinstance(r, dict)
         and str(r.get("serviceTag") or "").casefold() == serial.casefold()),
        records[0] if records and isinstance(records[0], dict) else None,
    )
    if not record or record.get("invalid"):
        return WarrantyResult("not_found", "dell", "Dell does not recognise this service tag.")
    start, end, description = _coverage(
        record.get("entitlements") or [], ("startDate",), ("endDate",)
    )
    return _found("dell", start or _parse_date(record.get("shipDate")), end, description)


def parse_hp_warranty(payload: Any) -> WarrantyResult:
    records = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
    record = next((r for r in records if isinstance(r, dict)), None)
    if not record:
        return WarrantyResult("not_found", "hp", "HP does not recognise this serial number.")
    periods = list(record.get("offers") or [])
    periods.append(record)
    start, end, description = _coverage(
        periods,
        ("serviceObligationLineItemStartDate", "warrantyStartDate", "startDate"),
        ("serviceObligationLineItemEndDate", "warrantyEndDate", "endDate"),
    )
    if not end:
        reason = str(record.get("statusMessage") or record.get("message") or "").strip()
        if reason:
            return WarrantyResult("not_found", "hp", ("HP: " + reason)[:255])
    return _found("hp", start, end, description)


def parse_lenovo_support_site(payload: Any) -> WarrantyResult:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or (isinstance(payload, dict) and payload.get("code") not in (0, "0", None)):
        return WarrantyResult("not_found", "lenovo", "Lenovo does not recognise this serial number.")
    periods = [*(data.get("baseWarranties") or []), *(data.get("upmaWarranties") or [])]
    start, end, description = _coverage(periods, ("startDate", "start"), ("endDate", "end"))
    return _found("lenovo", start, end, description)


def parse_lenovo_api(payload: Any) -> WarrantyResult:
    if not isinstance(payload, dict) or payload.get("ErrorCode") not in (0, "0", None):
        return WarrantyResult("not_found", "lenovo", "Lenovo does not recognise this serial number.")
    start, end, description = _coverage(payload.get("Warranty") or [], ("Start",), ("End",))
    return _found("lenovo", start, end, description)


# ---------------------------------------------------------------------------
# Vendor clients
# ---------------------------------------------------------------------------


class _VendorSession:
    """One HTTP client and cached OAuth tokens shared across a lookup run."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._tokens: dict[str, str] = {}
        self._last_request = 0.0

    async def __aenter__(self) -> "_VendorSession":
        self._client = monitored_client(
            httpx.AsyncClient, timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._client is None:
            raise RuntimeError("Vendor session is not open")
        async with _request_lock:
            loop = asyncio.get_running_loop()
            wait = self._last_request + _REQUEST_INTERVAL_SECONDS - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self._client.request(method, url, **kwargs)
            finally:
                self._last_request = loop.time()

    async def _oauth_token(
        self, vendor: str, url: str, client_id: str, client_secret: str, *, basic_auth: bool
    ) -> str:
        """Fetch a client-credentials token: Dell takes the credentials in the
        form body, HP as HTTP Basic authentication."""
        if vendor not in self._tokens:
            if basic_auth:
                response = await self.request(
                    "POST", url, auth=(client_id, client_secret),
                    data={"grant_type": "client_credentials"},
                )
            else:
                response = await self.request(
                    "POST", url,
                    data={"grant_type": "client_credentials", "client_id": client_id,
                          "client_secret": client_secret},
                )
            response.raise_for_status()
            token = str((response.json() or {}).get("access_token") or "")
            if not token:
                raise httpx.HTTPError(f"{VENDOR_LABELS[vendor]} did not issue an access token")
            self._tokens[vendor] = token
        return self._tokens[vendor]

    async def dell(self, serial: str) -> WarrantyResult:
        settings = get_settings()
        client_id = (settings.warranty_lookup_dell_client_id or "").strip()
        secret = (settings.warranty_lookup_dell_client_secret or "").strip()
        if not client_id or not secret:
            return WarrantyResult(
                "not_configured", "dell",
                "Add Dell TechDirect Warranty API credentials (WARRANTY_LOOKUP_DELL_*) to look up Dell assets.",
            )
        token = await self._oauth_token("dell", DELL_AUTH_URL, client_id, secret, basic_auth=False)
        response = await self.request(
            "GET", DELL_ENTITLEMENTS_URL, params={"servicetags": serial},
            headers={"Authorization": "Bearer " + token},
        )
        response.raise_for_status()
        return parse_dell_entitlements(response.json(), serial)

    async def hp(self, serial: str) -> WarrantyResult:
        settings = get_settings()
        api_key = (settings.warranty_lookup_hp_api_key or "").strip()
        secret = (settings.warranty_lookup_hp_api_secret or "").strip()
        if not api_key or not secret:
            return WarrantyResult(
                "not_configured", "hp",
                "Add HP Warranty API credentials (WARRANTY_LOOKUP_HP_*) to look up HP assets.",
            )
        token = await self._oauth_token("hp", HP_AUTH_URL, api_key, secret, basic_auth=True)
        response = await self.request(
            "POST", HP_QUERY_URL, json=[{"sn": serial}],
            headers={"Authorization": "Bearer " + token},
        )
        if response.status_code in (400, 404):
            return WarrantyResult("not_found", "hp", "HP does not recognise this serial number.")
        response.raise_for_status()
        return parse_hp_warranty(response.json())

    async def lenovo(self, serial: str) -> WarrantyResult:
        client_id = (get_settings().warranty_lookup_lenovo_client_id or "").strip()
        if client_id:
            response = await self.request(
                "GET", LENOVO_API_URL, params={"Serial": serial}, headers={"ClientID": client_id},
            )
            if response.status_code == 404:
                return WarrantyResult("not_found", "lenovo", "Lenovo does not recognise this serial number.")
            response.raise_for_status()
            return parse_lenovo_api(response.json())
        response = await self.request(
            "POST", LENOVO_SUPPORT_SITE_URL,
            json={"serialNumber": serial, "country": "us", "language": "en"},
        )
        if response.status_code == 404:
            return WarrantyResult("not_found", "lenovo", "Lenovo does not recognise this serial number.")
        response.raise_for_status()
        return parse_lenovo_support_site(response.json())

    async def lookup(self, vendor: str | None, serial: str) -> WarrantyResult:
        if not vendor:
            return WarrantyResult(
                "unsupported", None, "The manufacturer is unknown, so the warranty cannot be looked up.",
            )
        if vendor not in SUPPORTED_VENDORS:
            label = VENDOR_LABELS.get(vendor, vendor)
            url = VENDOR_CHECK_URLS.get(vendor)
            where = f" Check it at {url} and enter the dates manually." if url else ""
            return WarrantyResult(
                "unsupported", vendor, f"{label} has no free automated warranty lookup.{where}",
            )
        if not serial or asset_repo.is_placeholder_serial(serial):
            return WarrantyResult("not_found", vendor, "The asset has no usable serial number.")
        if not _SERIAL_RE.match(serial):
            return WarrantyResult("not_found", vendor, "The serial number is not in a format vendors accept.")
        try:
            return await getattr(self, vendor)(serial)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            reason = "rate limited" if code == 429 else (
                "credentials were rejected" if code in (401, 403) else f"HTTP {code}"
            )
            return WarrantyResult("error", vendor, f"{VENDOR_LABELS[vendor]} lookup failed: {reason}.")
        except (httpx.HTTPError, ValueError) as exc:
            log_error("Warranty lookup request failed", vendor=vendor, error=str(exc))
            return WarrantyResult("error", vendor, f"{VENDOR_LABELS[vendor]} lookup failed: service unavailable.")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def is_module_enabled() -> bool:
    try:
        module = await modules_service.get_module(MODULE_SLUG, redact=True)
    except Exception:  # pragma: no cover - defensive
        return False
    return bool(module and module.get("enabled"))


def vendor_for_asset(asset: dict[str, Any], hardware: dict[str, Any] | None = None) -> str | None:
    hardware = hardware or {}
    return detect_vendor(
        hardware.get("manufacturer"), asset.get("motherboard_manufacturer"),
        hardware.get("model"), asset.get("machine_type"),
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _record(asset_id: int, result: WarrantyResult) -> None:
    status_text = None
    if result.end_date:
        status_text = "Active" if result.end_date >= date.today() else "Expired"
    await asset_repo.record_warranty_lookup(
        asset_id, lookup_status=result.status, message=result.message, checked_at=_utcnow(),
        source=result.vendor, start_date=result.start_date, end_date=result.end_date,
        warranty_status=status_text,
    )


async def _lookup_assets(assets: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"checked": 0, "found": 0, "not_found": 0, "unsupported": 0, "not_configured": 0, "error": 0}
    hardware = await asset_repo.get_rmm_hardware_for_assets([int(a["id"]) for a in assets])
    async with _VendorSession() as session:
        for asset in assets:
            asset_id = int(asset["id"])
            info = hardware.get(asset_id) or {}
            serial = str(asset.get("serial_number") or info.get("serial_number") or "").strip()
            result = await session.lookup(vendor_for_asset(asset, info), serial)
            await _record(asset_id, result)
            summary["checked"] += 1
            summary[result.status] = summary.get(result.status, 0) + 1
    return summary


async def lookup_asset(asset_id: int) -> WarrantyResult:
    """Look up one asset now, regardless of when it was last checked."""
    if not await is_module_enabled():
        raise WarrantyLookupDisabledError("Enable the Warranty lookup module to check warranties.")
    asset = await asset_repo.get_asset_by_id(asset_id)
    if not asset:
        raise LookupError("Asset not found")
    info = (await asset_repo.get_rmm_hardware_for_assets([asset_id])).get(asset_id) or {}
    serial = str(asset.get("serial_number") or info.get("serial_number") or "").strip()
    async with _VendorSession() as session:
        result = await session.lookup(vendor_for_asset(asset, info), serial)
    await _record(asset_id, result)
    return result


async def refresh_due_assets(company_id: int | None = None) -> dict[str, Any]:
    """Look up every asset not checked within ``WARRANTY_LOOKUP_REFRESH_DAYS``."""
    if not await is_module_enabled():
        return {"status": "skipped", "reason": "Warranty lookup module is disabled"}
    settings = get_settings()
    assets = await asset_repo.list_assets_due_for_warranty_lookup(
        stale_before=_utcnow() - timedelta(days=settings.warranty_lookup_refresh_days),
        limit=settings.warranty_lookup_batch_size,
        company_id=company_id,
    )
    summary = await _lookup_assets(assets)
    log_info("Asset warranty lookup completed", company_id=company_id, **summary)
    return {"status": "ok", **summary}
