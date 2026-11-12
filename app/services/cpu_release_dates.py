"""Estimate how old each device is from its processor's launch date.

The exact purchase date of a computer is rarely known, but the processor it
reports gives a lower bound: a machine cannot be older than its CPU. Each
distinct processor model in the portal is registered once in
``cpu_release_dates``; once a launch date is recorded for it, every asset with
that processor gets the date in ``assets.cpu_release_date`` and the whole years
since then in ``assets.approx_age``. Ages are recalculated daily so they keep
counting up.

Launch dates are recorded by a technician on **Assets → CPU launch dates**.
For Intel processors the source is Intel ARK: the page links each model to
Intel's site, where the technician reads the *Launch Date* (for example
``Q3'19``) and types it in as shown. Intel no longer serves ARK to automated
clients (its old search redirects to a 404 page and intel.com answers scripts
with "Access Denied"), so MyPortal does not fetch it itself.

The daily job is gated by the ``cpu-age-lookup`` module toggle.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

from app.core.logging import log_info
from app.repositories import cpu_release_dates as repo
from app.services import modules as modules_service

MODULE_SLUG = "cpu-age-lookup"

# Intel's site search, which finds ARK specification pages, opened in the
# technician's own browser.
INTEL_ARK_SEARCH_URL = "https://www.intel.com/content/www/us/en/search.html?ws=text#q="
INTEL_HOSTS = ("intel.com",)

SOURCES = ("intel_ark", "manual")
SOURCE_LABELS = {"intel_ark": "Intel ARK", "manual": "Other source"}
VENDOR_LABELS = {"intel": "Intel", "amd": "AMD", "apple": "Apple", "qualcomm": "Qualcomm", "arm": "ARM"}


class LaunchDateError(ValueError):
    """Raised when a launch date or source link a technician entered is not usable."""


@dataclass(frozen=True)
class CpuIdentity:
    key: str
    vendor: str | None
    model: str | None
    name: str


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
# Launch dates as typed by a technician
# ---------------------------------------------------------------------------

# Single optional spaces keep matching linear (input is whitespace-collapsed first).
_QUARTER_RE = re.compile(r"^Q([1-4])\s?['’`]?\s?(\d{4}|\d{2})$", re.I)
_MAX_INPUT_LENGTH = 32
_ISO_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})(?:-\d{1,2})?$")
_MONTH_YEAR_RE = re.compile(r"^(\d{1,2})\s*/\s*(\d{4})$")
_NAMED_MONTH_RE = re.compile(r"^([a-z]{3,9})\.?\s+(\d{4})$", re.I)
_YEAR_RE = re.compile(r"^(19[89]\d|20\d{2})$")
_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def _full_year(text: str) -> int:
    year = int(text)
    if year < 100:
        year += 1900 if year >= 70 else 2000
    return year


def _month(year: int, month: int) -> tuple[date, str] | None:
    if not 1 <= month <= 12 or not 1970 <= year <= 2100:
        return None
    released = date(year, month, 1)
    return released, f"{released:%b %Y}"


def parse_release_text(text: Any) -> tuple[date, str] | None:
    """Read a launch date the way Intel ARK and technicians write it.

    Accepts ``Q3'19`` (as ARK shows it), ``Q3 2019``, ``2019-07``,
    ``07/2019``, ``Jul 2019`` or ``2019``. Quarters map to their first month,
    the earliest the processor could ship. Returns ``(date, label)``.
    """
    value = " ".join(html.unescape(str(text or ""))[:256].split())
    if not value or len(value) > _MAX_INPUT_LENGTH:
        return None
    match = _QUARTER_RE.match(value)
    if match:
        quarter, year = int(match.group(1)), _full_year(match.group(2))
        if not 1970 <= year <= 2100:
            return None
        return date(year, (quarter - 1) * 3 + 1, 1), f"Q{quarter} {year}"
    match = _ISO_MONTH_RE.match(value)
    if match:
        return _month(int(match.group(1)), int(match.group(2)))
    match = _MONTH_YEAR_RE.match(value)
    if match:
        return _month(int(match.group(2)), int(match.group(1)))
    match = _NAMED_MONTH_RE.match(value)
    if match and match.group(1)[:3].lower() in _MONTHS:
        return _month(int(match.group(2)), _MONTHS[match.group(1)[:3].lower()])
    match = _YEAR_RE.match(value)
    if match:
        return date(int(match.group(1)), 1, 1), match.group(1)
    return None


def ark_search_url(model: str | None) -> str | None:
    """Link to Intel's site search for a processor model, to open in a browser."""
    model = (model or "").strip()
    return INTEL_ARK_SEARCH_URL + quote(model) if model else None


def clean_source_url(value: Any) -> str | None:
    """Accept an optional https link to the Intel page the date was read from."""
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host == h or host.endswith("." + h) for h in INTEL_HOSTS):
        raise LaunchDateError("Paste the https:// link of the processor's page on intel.com.")
    if len(text) > 512:
        raise LaunchDateError("That link is too long.")
    return text


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


async def refresh() -> dict[str, Any]:
    """Register new processor models and recalculate every asset's age."""
    if not await is_module_enabled():
        return {"status": "skipped", "reason": "CPU age lookup module is disabled"}
    assets = await discover_cpus()
    summary = {"processors": len({a["_cpu"].key for a in assets if a.get("_cpu")}),
               "assets_updated": await apply_to_assets(assets)}
    log_info("CPU age refresh completed", **summary)
    return {"status": "ok", **summary}


async def overview(today: date | None = None) -> list[dict[str, Any]]:
    """Every processor model in use, with its launch date and how many devices have it.

    New models are registered first, so a technician can record a date for a
    processor as soon as an asset reports it.
    """
    assets = await discover_cpus()
    counts: dict[str, int] = {}
    for asset in assets:
        identity = asset.get("_cpu")
        if identity:
            counts[identity.key] = counts.get(identity.key, 0) + 1
    entries = []
    for entry in await repo.list_all():
        released = entry.get("release_date")
        entries.append({
            **entry,
            "device_count": counts.get(entry["cpu_key"], 0),
            "age_years": age_in_years(released, today) if released else None,
            "vendor_label": VENDOR_LABELS.get(entry.get("vendor") or "", "Unknown maker"),
            "source_label": SOURCE_LABELS.get(entry.get("source") or ""),
            "ark_url": ark_search_url(entry.get("model")) if entry.get("vendor") == "intel" else None,
        })
    return entries


async def set_release_date(
    entry_id: int,
    release: tuple[date, str] | None,
    *,
    source: str = "manual",
    source_url: str | None = None,
) -> dict[str, Any]:
    """Record (or clear, with ``release=None``) a processor's launch date and update its devices."""
    entry = await repo.get(entry_id)
    if not entry:
        raise LookupError("Processor not found")
    if source not in SOURCES:
        raise LaunchDateError("Choose where the launch date came from.")
    if release is None:
        await repo.set_manual(entry_id, release_date=None, release_label=None)
    else:
        released, label = release
        await repo.set_manual(
            entry_id, release_date=released, release_label=label, source=source,
            source_url=clean_source_url(source_url),
        )
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
        "source_label": SOURCE_LABELS.get(entry.get("source") or "", "Recorded"),
        "source_url": entry.get("source_url"),
        "model": entry.get("model") or entry.get("cpu_name"),
        "age_years": age_in_years(released),
    }
