"""Estimate how old each device is from its processor's launch date.

The exact purchase date of a computer is rarely known, but the processor it
reports gives a lower bound: a machine cannot be older than its CPU. Each
distinct processor model in the portal is looked up once and cached in
``cpu_release_dates``; every asset with that processor then gets the launch
date in ``assets.cpu_release_date`` and the whole years since then in
``assets.approx_age``. Ages are recalculated on every run so they keep
counting up.

Intel processors are always looked up on Intel ARK (``ark.intel.com``), which
is Intel's own processor specification database. Only the model number (for
example ``i7-8650U``) is sent. Other vendors publish no comparable lookup, so
their processors are listed as ``unsupported`` and a technician records the
launch date once per model, which then applies to every device with it.

The service is gated by the ``cpu-age-lookup`` module toggle.
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urljoin

import httpx

from app.core.config import get_settings
from app.core.logging import log_error, log_info
from app.repositories import cpu_release_dates as repo
from app.services import modules as modules_service
from app.services.monitored_http import monitored_client

MODULE_SLUG = "cpu-age-lookup"
REQUEST_TIMEOUT = 20.0
# Intel ARK is a public website; stay well under one request per second.
_REQUEST_INTERVAL_SECONDS = 2.0
_request_lock = asyncio.Lock()
_USER_AGENT = "Mozilla/5.0 (compatible; MyPortal-CPULookup/1.0)"

ARK_BASE_URL = "https://ark.intel.com"
ARK_SEARCH_URL = ARK_BASE_URL + "/content/www/us/en/ark/search.html?_charset_=UTF-8&q="

SOURCE_LABELS = {"intel_ark": "Intel ARK", "manual": "Entered manually"}
VENDOR_LABELS = {"intel": "Intel", "amd": "AMD", "apple": "Apple", "qualcomm": "Qualcomm", "arm": "ARM"}


class CpuLookupDisabledError(RuntimeError):
    """Raised when the CPU age lookup module is switched off."""


@dataclass(frozen=True)
class CpuIdentity:
    key: str
    vendor: str | None
    model: str | None
    name: str


@dataclass(frozen=True)
class LookupResult:
    status: str  # found, not_found, unsupported, error
    message: str
    release_date: date | None = None
    release_label: str | None = None
    source: str | None = None
    source_url: str | None = None


# ---------------------------------------------------------------------------
# CPU name parsing
# ---------------------------------------------------------------------------

_NOISE_RE = re.compile(
    r"\((?:r|tm|c)\)|®|™|\bcpu\b|\bprocessor\b|\bapu\b|@\s*[\d.]+\s*[gm]hz|\b[\d.]+\s*[gm]hz\b"
    r"|\b\d+(?:th|st|nd|rd)\s+gen(?:eration)?\b|\bwith\s+radeon.*$|\b\d+-core\b|\bmobile\b",
    re.IGNORECASE,
)
_VENDOR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("intel", re.compile(r"\bintel\b|\bgenuineintel\b|\bxeon\b|\bpentium\b|\bceleron\b|\bcore\s*(?:\(tm\))?\s*(?:i[3579]|ultra|2)\b|\batom\b", re.I)),
    ("amd", re.compile(r"\bamd\b|\bauthenticamd\b|\bryzen\b|\bathlon\b|\bepyc\b|\bthreadripper\b|\bopteron\b|\bphenom\b", re.I)),
    ("apple", re.compile(r"\bapple\s+m\d", re.I)),
    ("qualcomm", re.compile(r"\bqualcomm\b|\bsnapdragon\b", re.I)),
)
# Ordered most specific first; each returns the model as Intel ARK names it.
_INTEL_MODEL_PATTERNS: tuple[tuple[re.Pattern[str], Any], ...] = (
    (re.compile(r"\bultra\s*([3579])\s+(\d{3}[a-z]{0,2})\b", re.I),
     lambda m: f"Ultra {m.group(1)} {m.group(2).upper()}"),
    (re.compile(r"\b(i[3579])[- ]([a-z]?\d{3,5}[a-z]{0,3}\d?)\b", re.I),
     lambda m: f"{m.group(1).lower()}-{m.group(2).upper()}"),
    (re.compile(r"\b(e[357])-(\d{4}[a-z]?)(?:\s*(v\d+))?\b", re.I),
     lambda m: f"{m.group(1).upper()}-{m.group(2).upper()}" + (f" {m.group(3).lower()}" if m.group(3) else "")),
    (re.compile(r"\b(platinum|gold|silver|bronze)\s+(\d{4}[a-z]{0,2})\b", re.I),
     lambda m: f"{m.group(1).title()} {m.group(2).upper()}"),
    (re.compile(r"\b([ew])-(\d{4,5}[a-z]{0,2})\b", re.I),
     lambda m: f"{m.group(1).upper()}-{m.group(2).upper()}"),
    (re.compile(r"\b([a-z]{1,2}\d{3,5}[a-z]{0,2})\b", re.I),
     lambda m: m.group(1).upper()),
)
_AMD_MODEL_RE = re.compile(
    r"\b((?:ryzen(?:\s+ai)?(?:\s+(?:threadripper(?:\s+pro)?|pro))?\s+\d(?:\s+pro)?|athlon(?:\s+(?:silver|gold|pro))?|epyc|a\d{1,2}(?:\s+pro)?|fx|phenom\s+ii(?:\s+x\d)?|opteron)"
    r"[\s-]+[a-z]?\d{3,5}[a-z0-9]{0,4})\b",
    re.I,
)


def clean_cpu_name(name: Any) -> str:
    text = html.unescape(str(name or ""))
    text = _NOISE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" ,-")


def detect_vendor(name: Any) -> str | None:
    text = str(name or "")
    for vendor, pattern in _VENDOR_PATTERNS:
        if pattern.search(text):
            return vendor
    return None


def _intel_model(cleaned: str) -> str | None:
    for pattern, build in _INTEL_MODEL_PATTERNS:
        for match in pattern.finditer(cleaned):
            return build(match)
    return None


def _amd_model(cleaned: str) -> str | None:
    match = _AMD_MODEL_RE.search(cleaned)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip()


def identify(name: Any) -> CpuIdentity | None:
    """Return the vendor, model and cache key for a reported CPU name."""
    raw = re.sub(r"\s+", " ", html.unescape(str(name or ""))).strip()
    if not raw:
        return None
    vendor = detect_vendor(raw)
    cleaned = clean_cpu_name(raw)
    model: str | None = None
    if vendor == "intel":
        model = _intel_model(cleaned)
    elif vendor == "amd":
        model = _amd_model(cleaned)
    elif vendor == "apple":
        found = re.search(r"\bm\d+(?:\s+(?:pro|max|ultra))?\b", cleaned, re.I)
        if found:
            first, *rest = found.group(0).split()
            model = " ".join([first.upper(), *(part.title() for part in rest)])
    basis = (model or cleaned or raw).casefold()
    key = f"{vendor or 'unknown'}:{re.sub(r'\s+', ' ', basis)}"[:191]
    return CpuIdentity(key=key, vendor=vendor, model=model, name=raw[:255])


# ---------------------------------------------------------------------------
# Intel ARK parsing
# ---------------------------------------------------------------------------

_QUARTER_RE = re.compile(r"\bQ([1-4])\s*['’]?\s*(\d{4}|\d{2})\b", re.I)
_MONTH_YEAR_RE = re.compile(r"\b(\d{1,2})/(\d{4})\b")
_YEAR_RE = re.compile(r"\b(19[89]\d|20\d{2})\b")
_LAUNCH_MARKERS = (
    re.compile(r"data-key=[\"']BornOnDate[\"'][^>]*>", re.I),
    re.compile(r">\s*Launch Date\s*<", re.I),
)
_TAG_RE = re.compile(r"<[^>]+>")
_PRODUCT_LINK_RE = re.compile(
    r"href=[\"']((?:https?://(?:ark|www)\.intel\.com)?/content/www/[a-z]{2}/[a-z]{2}/"
    r"(?:ark/products|products/sku)/\d+/[^\"'#?]+?\.html)[\"']",
    re.I,
)


def _full_year(text: str) -> int:
    year = int(text)
    if year < 100:
        year += 1900 if year >= 70 else 2000
    return year


def parse_release_text(text: str) -> tuple[date, str] | None:
    """Turn an ARK launch date ("Q3'17", "Q1 2020", "04/2016", "2015") into a date.

    Quarters map to their first day, the earliest the processor could ship.
    """
    text = html.unescape(text or "").strip()
    match = _QUARTER_RE.search(text)
    if match:
        quarter, year = int(match.group(1)), _full_year(match.group(2))
        return date(year, (quarter - 1) * 3 + 1, 1), f"Q{quarter} {year}"
    match = _MONTH_YEAR_RE.search(text)
    if match and 1 <= int(match.group(1)) <= 12:
        month, year = int(match.group(1)), int(match.group(2))
        return date(year, month, 1), f"{date(year, month, 1):%b %Y}"
    match = _YEAR_RE.search(text)
    if match:
        year = int(match.group(1))
        return date(year, 1, 1), str(year)
    return None


def parse_ark_launch_date(page: str) -> tuple[date, str] | None:
    """Read the "Launch Date" value from an Intel ARK product page.

    The value follows the ``data-key="BornOnDate"`` attribute (or, failing
    that, the "Launch Date" label) and reads like ``Q3'17``.
    """
    page = page or ""
    for marker_re in _LAUNCH_MARKERS:
        for marker in marker_re.finditer(page):
            following = _TAG_RE.sub(" ", page[marker.end(): marker.end() + 600])
            parsed = parse_release_text(" ".join(following.split()[:4]))
            if parsed:
                return parsed
    return None


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def find_ark_product_url(page: str, model: str) -> str | None:
    """Pick the product page link whose URL names ``model`` from ARK search results."""
    wanted = _compact(model)
    first: str | None = None
    for match in _PRODUCT_LINK_RE.finditer(page or ""):
        url = urljoin(ARK_BASE_URL, match.group(1))
        if first is None:
            first = url
        slug = _compact(url.rsplit("/", 1)[-1])
        if wanted and wanted in slug:
            return url
    # A single, unambiguous result is still the product the search matched.
    links = {m.group(1) for m in _PRODUCT_LINK_RE.finditer(page or "")}
    return first if len(links) == 1 else None


# ---------------------------------------------------------------------------
# Intel ARK client
# ---------------------------------------------------------------------------


class _ArkSession:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._last_request = 0.0

    async def __aenter__(self) -> "_ArkSession":
        self._client = monitored_client(
            httpx.AsyncClient, timeout=REQUEST_TIMEOUT, follow_redirects=True,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml",
                     "Accept-Language": "en-US,en;q=0.8"},
        )
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def get(self, url: str) -> httpx.Response:
        if self._client is None:
            raise RuntimeError("ARK session is not open")
        async with _request_lock:
            loop = asyncio.get_running_loop()
            wait = self._last_request + _REQUEST_INTERVAL_SECONDS - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self._client.get(url)
            finally:
                self._last_request = loop.time()

    async def intel(self, model: str) -> LookupResult:
        response = await self.get(ARK_SEARCH_URL + quote(model))
        response.raise_for_status()
        page = response.text
        product_url = str(response.url)
        launch = parse_ark_launch_date(page)
        if not launch:
            link = find_ark_product_url(page, model)
            if not link:
                return LookupResult("not_found", f"Intel ARK has no processor matching {model}.")
            response = await self.get(link)
            response.raise_for_status()
            product_url = str(response.url)
            launch = parse_ark_launch_date(response.text)
        if not launch:
            return LookupResult(
                "not_found", f"Intel ARK lists {model} without a launch date.", source_url=product_url,
            )
        released, label = launch
        return LookupResult(
            "found", f"Intel ARK: {model} launched {label}.", released, label, "intel_ark", product_url,
        )

    async def lookup(self, identity: CpuIdentity) -> LookupResult:
        if identity.vendor != "intel":
            label = VENDOR_LABELS.get(identity.vendor or "", "This processor's maker")
            return LookupResult(
                "unsupported", f"{label} has no free automated launch-date lookup; record the date manually.",
            )
        if not identity.model:
            return LookupResult(
                "unsupported", "The CPU name has no Intel model number (for example a virtual CPU); record the date manually.",
            )
        try:
            return await self.intel(identity.model)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            reason = "rate limited" if code == 429 else ("access denied" if code in (401, 403) else f"HTTP {code}")
            return LookupResult("error", f"Intel ARK lookup failed: {reason}.")
        except (httpx.HTTPError, ValueError) as exc:
            log_error("Intel ARK lookup failed", model=identity.model, error=str(exc))
            return LookupResult("error", "Intel ARK lookup failed: service unavailable.")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def age_in_years(released: date, today: date | None = None) -> int:
    """Whole years since ``released``, rounded to the nearest year."""
    today = today or date.today()
    if released >= today:
        return 0
    return int(((today - released).days / 365.25) + 0.5)


async def is_module_enabled() -> bool:
    try:
        module = await modules_service.get_module(MODULE_SLUG, redact=True)
    except Exception:  # pragma: no cover - defensive
        return False
    return bool(module and module.get("enabled"))


def _asset_cpu_name(asset: dict[str, Any]) -> str:
    return str(asset.get("cpu_name") or "").strip() or str(asset.get("cpu_model") or "").strip()


async def discover_cpus() -> list[dict[str, Any]]:
    """Register every processor model reported by an active asset; returns the assets."""
    assets = await repo.list_asset_cpus()
    seen: set[str] = set()
    for asset in assets:
        identity = identify(_asset_cpu_name(asset))
        asset["_cpu"] = identity
        if identity and identity.key not in seen:
            seen.add(identity.key)
            await repo.ensure(identity.key, identity.name, identity.vendor, identity.model)
    return assets


async def look_up_due(limit: int | None = None) -> dict[str, int]:
    """Look up processors that have no launch date yet, oldest check first."""
    settings = get_settings()
    due = await repo.list_due(
        retry_before=_utcnow() - timedelta(days=settings.cpu_release_lookup_retry_days),
        limit=limit or settings.cpu_release_lookup_batch_size,
    )
    summary = {"checked": 0, "found": 0, "not_found": 0, "unsupported": 0, "error": 0}
    if not due:
        return summary
    async with _ArkSession() as session:
        for entry in due:
            identity = CpuIdentity(
                key=entry["cpu_key"], vendor=entry.get("vendor"), model=entry.get("model"),
                name=entry.get("cpu_name") or "",
            )
            result = await session.lookup(identity)
            await repo.record_lookup(
                identity.key, lookup_status=result.status, message=result.message,
                checked_at=_utcnow(), source=result.source, release_date=result.release_date,
                release_label=result.release_label, source_url=result.source_url,
            )
            summary["checked"] += 1
            summary[result.status] = summary.get(result.status, 0) + 1
    return summary


async def apply_to_assets(assets: list[dict[str, Any]] | None = None, today: date | None = None) -> int:
    """Store each asset's CPU launch date and the approximate age derived from it.

    Returns the number of assets changed.
    """
    if assets is None:
        assets = await repo.list_asset_cpus()
    dates = {entry["cpu_key"]: entry.get("release_date") for entry in await repo.list_all()}
    changed = 0
    for asset in assets:
        identity = asset.get("_cpu") if "_cpu" in asset else identify(_asset_cpu_name(asset))
        released = dates.get(identity.key) if identity else None
        current = asset.get("cpu_release_date")
        if released is None:
            if current is not None:
                await repo.set_asset_cpu_age(int(asset["id"]), release_date=None, approx_age=None)
                changed += 1
            continue
        age = age_in_years(released, today)
        existing_age = asset.get("approx_age")
        try:
            existing_age = int(round(float(existing_age))) if existing_age is not None else None
        except (TypeError, ValueError):
            existing_age = None
        if current != released or existing_age != age:
            await repo.set_asset_cpu_age(int(asset["id"]), release_date=released, approx_age=age)
            changed += 1
    return changed


async def refresh(*, limit: int | None = None) -> dict[str, Any]:
    """Find new processors, look up missing launch dates and update asset ages."""
    if not await is_module_enabled():
        return {"status": "skipped", "reason": "CPU age lookup module is disabled"}
    assets = await discover_cpus()
    summary = await look_up_due(limit)
    summary["assets_updated"] = await apply_to_assets(assets)
    log_info("CPU release date lookup completed", **summary)
    return {"status": "ok", **summary}


async def set_manual_release_date(entry_id: int, release_date: date | None) -> dict[str, Any]:
    """Record (or clear) a processor's launch date by hand and update its devices."""
    entry = await repo.get(entry_id)
    if not entry:
        raise LookupError("Processor not found")
    label = f"{release_date:%b %Y}" if release_date else None
    await repo.set_manual(entry_id, release_date=release_date, release_label=label)
    await apply_to_assets()
    return await repo.get(entry_id) or entry


def describe(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Summarise where a device's estimated age came from, for the asset page."""
    released = entry.get("release_date") if entry else None
    if not released:
        return None
    return {
        "release_label": entry.get("release_label") or released.isoformat(),
        "release_date": released.isoformat(),
        "source_label": SOURCE_LABELS.get(entry.get("source") or "", "Lookup"),
        "source_url": entry.get("source_url"),
        "model": entry.get("model") or entry.get("cpu_name"),
        "age_years": age_in_years(released),
    }
