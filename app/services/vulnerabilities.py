"""Vulnerability management: known CVEs in the software RMM devices report.

How it works:

1. **Mapping.** Installed programs (``rmm_device_software``, from the RMM
   agent's daily inventory) are mapped to NVD CPE ``vendor:product`` pairs by
   name: a built-in list of common third-party apps (:data:`BUILTIN_MAPPINGS`)
   plus mappings super admins add (``vuln_software_mappings``). Programs from
   Linux distribution package managers are left out, because distributions
   backport security fixes without changing the upstream version, so matching
   them against NVD version ranges would flag patched packages. Windows and
   Microsoft Office updates are covered by Windows update scanning instead.
2. **NVD.** For each mapped product installed somewhere, the product's CVEs
   are downloaded from the NVD CVE API 2.0 (``virtualMatchString``) with their
   vulnerable version ranges, then refreshed daily with only the CVEs modified
   since (``lastModStartDate``). ``NVD_API_KEY`` raises NVD's rate limit; without
   it requests are spaced :data:`NVD_DELAY_NO_KEY` seconds apart.
3. **KEV.** CISA's Known Exploited Vulnerabilities catalogue is downloaded
   daily; a CVE on it is shown as *actively exploited*, with CISA's due date
   and whether it is known to be used in ransomware.
4. **Matching.** Each installed version is compared with every affected range
   of its product (numeric parts, ``1.10`` > ``1.9``). Matches become findings
   per device; findings no longer matched (the software was updated or
   removed) are marked resolved. A company can accept the risk of one CVE, or
   every CVE in a product, with a reason and an optional expiry; its findings
   then show as accepted.

The work runs on the RMM automation tick (:func:`tick`) once a day, or when a
super admin asks for it, in the background and on one instance at a time.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from loguru import logger

from app.core.config import get_settings
from app.repositories import vulnerabilities as repo
from app.services.monitored_http import monitored_client

NVD_CVE_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
NVD_CPE_URL = "https://services.nvd.nist.gov/rest/json/cpes/2.0"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
NVD_PAGE_SIZE = 2000
NVD_DELAY_NO_KEY = 6.5
NVD_DELAY_WITH_KEY = 0.7
NVD_MAX_INCREMENTAL = timedelta(days=110)  # NVD allows at most 120 days per lastMod range
SYNC_INTERVAL = timedelta(hours=24)
RUN_TIME_LIMIT = timedelta(minutes=45)
STALE_RUN = timedelta(hours=2)
HTTP_TIMEOUT = 60.0
DISTRO_SOURCES = ("dpkg", "rpm", "pacman")
MATCH_TYPES = {"exact": "Is exactly", "starts_with": "Starts with", "contains": "Contains"}
SEVERITIES = ("critical", "high", "medium", "low", "none")
SEVERITY_LABELS = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "none": "Unscored"}
MAX_REASON_LENGTH = 2000

_CPE_PART = re.compile(r"^[A-Za-z0-9._\-~!\\+]{1,128}$")
_CVE_ID = re.compile(r"^CVE-\d{4}-\d{4,}$")


@dataclass(frozen=True)
class Mapping_:
    match_type: str
    pattern: str
    vendor: str
    product: str
    builtin: bool = False
    id: int | None = None

    def matches(self, name: str) -> bool:
        text, pattern = name.casefold().strip(), self.pattern.casefold().strip()
        if self.match_type == "exact":
            return text == pattern
        if self.match_type == "contains":
            return pattern in text
        return text.startswith(pattern)


# Common third-party apps whose installed names and versions line up with
# NVD's CPE names and version ranges.
BUILTIN_MAPPINGS: tuple[Mapping_, ...] = tuple(Mapping_(kind, pattern, vendor, product, builtin=True) for kind, pattern, vendor, product in (
    ("starts_with", "Google Chrome", "google", "chrome"),
    ("starts_with", "Mozilla Firefox", "mozilla", "firefox"),
    ("starts_with", "Mozilla Thunderbird", "mozilla", "thunderbird"),
    ("exact", "Microsoft Edge", "microsoft", "edge_chromium"),
    ("starts_with", "7-Zip", "7-zip", "7-zip"),
    ("starts_with", "Adobe Acrobat Reader", "adobe", "acrobat_reader_dc"),
    ("starts_with", "Notepad++", "notepad-plus-plus", "notepad\\+\\+"),
    ("starts_with", "VLC media player", "videolan", "vlc_media_player"),
    ("starts_with", "PuTTY", "simon_tatham", "putty"),
    ("starts_with", "WinRAR", "rarlab", "winrar"),
    ("starts_with", "WinSCP", "winscp", "winscp"),
    ("starts_with", "FileZilla", "filezilla-project", "filezilla_client"),
    ("starts_with", "Wireshark", "wireshark", "wireshark"),
    ("starts_with", "TeamViewer", "teamviewer", "teamviewer"),
    ("exact", "AnyDesk", "anydesk", "anydesk"),
    ("starts_with", "Node.js", "nodejs", "node.js"),
    ("exact", "Git", "git-scm", "git"),
    ("starts_with", "KeePass Password Safe", "keepass", "keepass"),
    ("exact", "VMware Tools", "vmware", "tools"),
    ("starts_with", "Citrix Workspace", "citrix", "workspace"),
    ("starts_with", "LibreOffice", "libreoffice", "libreoffice"),
    ("starts_with", "Foxit PDF Reader", "foxit", "pdf_reader"),
    ("exact", "iTunes", "apple", "itunes"),
    ("exact", "Docker Desktop", "docker", "desktop"),
))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str) and value:
        try:
            return _as_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #


def version_key(value: Any) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", str(value or ""))[:8])


def compare_versions(left: Any, right: Any) -> int:
    a, b = version_key(left), version_key(right)
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return (a > b) - (a < b)


def version_affected(version: Any, item: Mapping[str, Any]) -> bool:
    """Whether an installed version falls in one affected CPE range."""

    if not version_key(version):
        return False  # no usable version: never guess
    exact = item.get("version_exact")
    bounds = [item.get(key) for key in ("start_including", "start_excluding", "end_including", "end_excluding")]
    if exact not in (None, "", "*"):
        if exact == "-":
            return False
        return compare_versions(version, exact) == 0
    if not any(bounds):
        return exact == "*"
    if item.get("start_including") and compare_versions(version, item["start_including"]) < 0:
        return False
    if item.get("start_excluding") and compare_versions(version, item["start_excluding"]) <= 0:
        return False
    if item.get("end_including") and compare_versions(version, item["end_including"]) > 0:
        return False
    if item.get("end_excluding") and compare_versions(version, item["end_excluding"]) >= 0:
        return False
    return True


def fix_hint(ranges: Iterable[Mapping[str, Any]]) -> str | None:
    """The lowest version that fixes every matched range, in words."""

    excluding = [str(item["end_excluding"]) for item in ranges if item.get("end_excluding")]
    including = [str(item["end_including"]) for item in ranges if item.get("end_including")]
    if excluding:
        best = max(excluding, key=version_key)
        if including and max(version_key(value) for value in including) >= version_key(best):
            return f"Update to a version newer than {max(including, key=version_key)}"
        return f"Update to {best} or later"
    if including:
        return f"Update to a version newer than {max(including, key=version_key)}"
    return None


# --------------------------------------------------------------------------- #
# Mappings
# --------------------------------------------------------------------------- #


def parse_mapping_form(form: Mapping[str, Any]) -> dict[str, Any]:
    errors: dict[str, str] = {}
    match_type = str(form.get("match_type") or "starts_with")
    if match_type not in MATCH_TYPES:
        errors["match_type"] = "Choose how the program name is matched."
    pattern = " ".join(str(form.get("pattern") or "").split())[:255]
    if len(pattern) < 2:
        errors["pattern"] = "Enter the program name as the device lists it, such as Zoom Workplace."
    vendor = str(form.get("vendor") or "").strip().lower()
    product = str(form.get("product") or "").strip().lower()
    cpe = str(form.get("cpe") or "").strip()
    if cpe and not (vendor and product):
        parsed = parse_cpe(cpe)
        if parsed:
            vendor, product = parsed["vendor"], parsed["product"]
    if not _CPE_PART.match(vendor) or vendor in ("*", "-"):
        errors["vendor"] = "Enter the NVD vendor, such as zoom (find it with “Find in NVD”)."
    if not _CPE_PART.match(product) or product in ("*", "-"):
        errors["product"] = "Enter the NVD product, such as workplace."
    if errors:
        raise ValueError(errors)
    return {"match_type": match_type, "pattern": pattern, "vendor": vendor, "product": product,
            "is_enabled": str(form.get("is_enabled", "1")).lower() in {"1", "on", "true", "yes"}}


async def all_mappings() -> list[Mapping_]:
    custom = [Mapping_(row["match_type"], row["pattern"], row["vendor"], row["product"], id=row["id"])
              for row in await repo.list_mappings() if row["is_enabled"]]
    # A company's own mappings are checked first, so they can override a built-in one.
    return custom + list(BUILTIN_MAPPINGS)


def map_software(name: str, mappings: Sequence[Mapping_]) -> Mapping_ | None:
    return next((mapping for mapping in mappings if mapping.matches(name)), None)


# --------------------------------------------------------------------------- #
# Parsing NVD and KEV
# --------------------------------------------------------------------------- #


def parse_cpe(criteria: str) -> dict[str, str] | None:
    parts = re.split(r"(?<!\\):", str(criteria or ""))
    if len(parts) < 6 or parts[0] != "cpe" or parts[1] != "2.3":
        return None
    return {"part": parts[2], "vendor": parts[3], "product": parts[4], "version": parts[5]}


def _cvss(metrics: Mapping[str, Any]) -> tuple[float | None, str | None, str]:
    for key, version in (("cvssMetricV31", "3.1"), ("cvssMetricV30", "3.0"), ("cvssMetricV40", "4.0"),
                         ("cvssMetricV2", "2.0")):
        entries = metrics.get(key) or []
        if not entries:
            continue
        primary = next((entry for entry in entries if entry.get("type") == "Primary"), entries[0])
        data = primary.get("cvssData") or {}
        score = data.get("baseScore")
        severity = data.get("baseSeverity") or primary.get("baseSeverity")
        if score is not None:
            return float(score), version, str(severity or severity_for(float(score))).lower()
    return None, None, "none"


def severity_for(score: float) -> str:
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    return "low" if score > 0 else "none"


def parse_nvd_cve(item: Mapping[str, Any], vendor: str, product: str) -> dict[str, Any] | None:
    """One NVD vulnerability with only the vulnerable ranges for ``vendor:product``."""

    cve = item.get("cve") or {}
    cve_id = str(cve.get("id") or "")
    if not _CVE_ID.match(cve_id) or str(cve.get("vulnStatus") or "").lower() == "rejected":
        return None
    ranges: list[dict[str, Any]] = []
    for configuration in cve.get("configurations") or []:
        for node in configuration.get("nodes") or []:
            if node.get("negate"):
                continue
            for match in node.get("cpeMatch") or []:
                if not match.get("vulnerable"):
                    continue
                cpe = parse_cpe(match.get("criteria") or "")
                if not cpe or cpe["part"] != "a" or cpe["vendor"] != vendor or cpe["product"] != product:
                    continue
                exact = cpe["version"]
                entry = {
                    "version_exact": exact if exact not in ("*",) else "*",
                    "start_including": match.get("versionStartIncluding"),
                    "start_excluding": match.get("versionStartExcluding"),
                    "end_including": match.get("versionEndIncluding"),
                    "end_excluding": match.get("versionEndExcluding"),
                }
                if any(entry[key] for key in ("start_including", "start_excluding", "end_including", "end_excluding")):
                    entry["version_exact"] = None
                entry = {key: (str(value)[:64] if value is not None else None) for key, value in entry.items()}
                if entry not in ranges:
                    ranges.append(entry)
    if not ranges:
        return None
    score, cvss_version, severity = _cvss(cve.get("metrics") or {})
    summary = next((entry.get("value") for entry in cve.get("descriptions") or [] if entry.get("lang") == "en"), "")
    return {
        "cve_id": cve_id,
        "published_at": _as_naive(cve.get("published")),
        "modified_at": _as_naive(cve.get("lastModified")),
        "cvss_score": score,
        "cvss_version": cvss_version,
        "severity": severity,
        "summary": str(summary or "")[:4000],
        "ranges": ranges,
    }


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def parse_kev(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for item in payload.get("vulnerabilities") or []:
        cve_id = str(item.get("cveID") or "").strip()
        if not _CVE_ID.match(cve_id):
            continue
        entries[cve_id] = {
            "cve_id": cve_id,
            "vendor_project": str(item.get("vendorProject") or "")[:255],
            "product": str(item.get("product") or "")[:255],
            "name": str(item.get("vulnerabilityName") or "")[:500],
            "date_added": _date(item.get("dateAdded")),
            "due_date": _date(item.get("dueDate")),
            "known_ransomware": str(item.get("knownRansomwareCampaignUse") or "").lower() == "known",
            "required_action": str(item.get("requiredAction") or "")[:2000],
        }
    return list(entries.values())


# --------------------------------------------------------------------------- #
# Downloading
# --------------------------------------------------------------------------- #


def _nvd_headers() -> dict[str, str]:
    key = str(getattr(get_settings(), "nvd_api_key", None) or "").strip()
    return {"apiKey": key} if key else {}


def nvd_delay() -> float:
    return NVD_DELAY_WITH_KEY if _nvd_headers() else NVD_DELAY_NO_KEY


def _nvd_time(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat() + ".000+00:00"


async def fetch_product_cves(client: httpx.AsyncClient, vendor: str, product: str, *,
                             since: datetime | None, now: datetime, sleep: Any = asyncio.sleep) -> list[dict[str, Any]]:
    """Every CVE affecting ``vendor:product`` (or only those modified since ``since``)."""

    params: dict[str, Any] = {"virtualMatchString": f"cpe:2.3:a:{vendor}:{product}",
                              "resultsPerPage": NVD_PAGE_SIZE, "startIndex": 0}
    if since is not None:
        params.update(lastModStartDate=_nvd_time(since), lastModEndDate=_nvd_time(now))
    found: list[dict[str, Any]] = []
    while True:
        response = await client.get(NVD_CVE_URL, params=params, headers=_nvd_headers())
        response.raise_for_status()
        payload = response.json()
        for item in payload.get("vulnerabilities") or []:
            parsed = parse_nvd_cve(item, vendor, product)
            if parsed:
                found.append(parsed)
        total = int(payload.get("totalResults") or 0)
        params["startIndex"] = int(payload.get("startIndex") or 0) + int(payload.get("resultsPerPage") or 0)
        if not payload.get("resultsPerPage") or params["startIndex"] >= total:
            return found
        await sleep(nvd_delay())


async def search_cpes(keyword: str, *, limit: int = 20) -> list[dict[str, str]]:
    """NVD's CPE dictionary entries for a keyword, for the mapping editor."""

    keyword = " ".join(str(keyword or "").split())[:100]
    if len(keyword) < 2:
        return []
    async with monitored_client(httpx.AsyncClient, timeout=HTTP_TIMEOUT) as client:
        response = await client.get(NVD_CPE_URL, params={"keywordSearch": keyword, "resultsPerPage": 100},
                                    headers=_nvd_headers())
        response.raise_for_status()
        payload = response.json()
    results: dict[tuple[str, str], dict[str, str]] = {}
    for item in payload.get("products") or []:
        cpe = item.get("cpe") or {}
        parsed = parse_cpe(cpe.get("cpeName") or "")
        if not parsed or parsed["part"] != "a":
            continue
        title = next((entry.get("title") for entry in cpe.get("titles") or [] if entry.get("lang") == "en"), "")
        key = (parsed["vendor"], parsed["product"])
        results.setdefault(key, {"vendor": key[0], "product": key[1], "title": str(title or "")[:255]})
        if len(results) >= limit:
            break
    return list(results.values())


async def refresh_kev(client: httpx.AsyncClient) -> int:
    response = await client.get(KEV_URL)
    response.raise_for_status()
    entries = parse_kev(response.json())
    if entries:
        await repo.replace_kev(entries)
    return len(entries)


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


async def installed_products(mappings: Sequence[Mapping_]) -> tuple[list[dict[str, Any]], set[tuple[str, str]]]:
    """Installed programs with their mapping, and the products in use."""

    rows = []
    products: set[tuple[str, str]] = set()
    cache: dict[str, Mapping_ | None] = {}
    for row in await repo.list_installed_software(DISTRO_SOURCES):
        name = str(row.get("name") or "")
        if name not in cache:
            cache[name] = map_software(name, mappings)
        mapping = cache[name]
        if mapping is None:
            continue
        rows.append({**row, "vendor": mapping.vendor, "product": mapping.product})
        products.add((mapping.vendor, mapping.product))
    return rows, products


def _exception_covers(exception: Mapping[str, Any], *, company_id: Any, vendor: str, product: str, cve_id: str,
                      now: datetime) -> bool:
    if company_id is None or int(exception["company_id"]) != int(company_id):
        return False
    if exception["vendor"] != vendor or exception["product"] != product:
        return False
    if exception.get("cve_id") and exception["cve_id"] != cve_id:
        return False
    expires = _as_naive(exception.get("expires_at"))
    return expires is None or expires > now


async def match_findings(now: datetime | None = None) -> dict[str, int]:
    """Compare installed versions with stored CVE ranges and update findings."""

    now = now or _utcnow()
    mappings = await all_mappings()
    software, products = await installed_products(mappings)
    ranges = await repo.list_product_ranges(products)
    by_product: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    for key, items in ranges.items():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            grouped[item["cve_id"]].append(item)
        by_product[key] = grouped
    exceptions = await repo.list_exceptions()
    existing = await repo.list_finding_keys()
    seen: set[int] = set()
    inserts: list[tuple[Any, ...]] = []
    refreshes: list[tuple[Any, ...]] = []
    for row in software:
        version = row.get("version")
        for cve_id, items in by_product.get((row["vendor"], row["product"]), {}).items():
            if not any(version_affected(version, item) for item in items):
                continue
            accepted = any(_exception_covers(item, company_id=row.get("company_id"), vendor=row["vendor"],
                                             product=row["product"], cve_id=cve_id, now=now) for item in exceptions)
            status = "accepted" if accepted else "open"
            key = (int(row["agent_id"]), cve_id, str(row["name"])[:255])
            current = existing.get(key)
            if current:
                if int(current["id"]) not in seen:
                    seen.add(int(current["id"]))
                    refreshes.append((row.get("asset_id"), row.get("company_id"), version, status, now, current["id"]))
            else:
                existing[key] = {"id": None}
                inserts.append((key[0], row.get("asset_id"), row.get("company_id"), cve_id, row["vendor"],
                                row["product"], key[2], version, status, now, now))
    resolved = [int(item["id"]) for item in existing.values()
                if item.get("id") is not None and int(item["id"]) not in seen and item.get("status") != "resolved"]
    await repo.insert_findings(inserts)
    await repo.refresh_findings(refreshes)
    await repo.resolve_findings(resolved, now)
    await repo.update_state(matched_at=now)
    return {"new": len(inserts), "still_open": len(refreshes), "resolved": len(resolved)}


# --------------------------------------------------------------------------- #
# Running a sync
# --------------------------------------------------------------------------- #


async def sync(*, now: datetime | None = None, force: bool = False,
               sleep: Any = asyncio.sleep) -> dict[str, Any]:
    """Refresh KEV and the CVEs of installed products, then match findings."""

    started = now or _utcnow()
    deadline = time.monotonic() + RUN_TIME_LIMIT.total_seconds()
    result: dict[str, Any] = {"products": 0, "cves": 0, "kev": None, "errors": []}
    state = await repo.get_state()
    async with monitored_client(httpx.AsyncClient, timeout=HTTP_TIMEOUT) as client:
        kev_synced = _as_naive(state.get("kev_synced_at"))
        if force or kev_synced is None or started - kev_synced >= SYNC_INTERVAL:
            try:
                result["kev"] = await refresh_kev(client)
                await repo.update_state(kev_synced_at=started)
            except (httpx.HTTPError, ValueError) as exc:
                result["errors"].append(f"CISA KEV: {exc}")
        mappings = await all_mappings()
        _software, products = await installed_products(mappings)
        known = await repo.list_products()
        for vendor, product in sorted(products):
            if time.monotonic() > deadline:
                result["errors"].append("Stopped for now; the rest continues on the next run.")
                break
            record = known.get((vendor, product)) or {}
            synced = _as_naive(record.get("synced_at"))
            if not force and synced is not None and started - synced < SYNC_INTERVAL:
                continue
            since = synced - timedelta(hours=1) if synced and started - synced < NVD_MAX_INCREMENTAL else None
            try:
                cves = await fetch_product_cves(client, vendor, product, since=since, now=started, sleep=sleep)
                await repo.save_cves(cves, vendor=vendor, product=product)
                await repo.save_product(vendor, product, synced_at=started, error=None)
                result["products"] += 1
                result["cves"] += len(cves)
            except (httpx.HTTPError, ValueError) as exc:
                message = f"{vendor}:{product}: {exc}"[:500]
                result["errors"].append(message)
                await repo.save_product(vendor, product, synced_at=None, error=message)
            await sleep(nvd_delay())
    result["findings"] = await match_findings(_utcnow())
    await repo.update_state(nvd_synced_at=started, last_error="; ".join(result["errors"])[:1000] or None)
    logger.info("Vulnerability sync finished: {}", result)
    return result


_running: set[asyncio.Task[Any]] = set()


async def _run_in_background(force: bool) -> None:
    try:
        await sync(force=force)
    except Exception as exc:  # noqa: BLE001 - recorded for the settings page
        logger.warning("Vulnerability sync failed: {}", exc)
        await repo.update_state(last_error=f"Sync failed: {exc}"[:1000])
    finally:
        await repo.update_state(running_since=None)


async def tick(now: datetime | None = None) -> bool:
    """Start a sync when one is due or was requested. Returns whether one started."""

    now = now or _utcnow()
    state = await repo.get_state()
    requested = state.get("requested_at") is not None
    last = _as_naive(state.get("nvd_synced_at"))
    if not requested and last is not None and now - last < SYNC_INTERVAL:
        return False
    if not await repo.claim_run(now, now - STALE_RUN):
        return False
    task = asyncio.create_task(_run_in_background(force=requested))
    _running.add(task)
    task.add_done_callback(_running.discard)
    return True


async def request_sync() -> None:
    await repo.update_state(requested_at=_utcnow())


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def _device_label(finding: Mapping[str, Any]) -> str:
    return str(finding.get("asset_name") or finding.get("hostname") or f"Device {finding.get('agent_id')}")


def group_by_software(findings: Sequence[Mapping[str, Any]], ranges: Mapping[tuple[str, str], list[dict[str, Any]]]
                      ) -> list[dict[str, Any]]:
    """Findings as the list shows them: one card per product, worst first."""

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for finding in findings:
        key = (finding["vendor"], finding["product"])
        group = groups.setdefault(key, {
            "vendor": key[0], "product": key[1], "names": set(), "versions": set(), "devices": {},
            "cves": {}, "open": 0, "accepted": 0, "kev": 0, "max_score": 0.0,
        })
        group["names"].add(finding["software_name"])
        if finding.get("software_version"):
            group["versions"].add(str(finding["software_version"]))
        group["devices"][finding.get("asset_id") or f"agent-{finding['agent_id']}"] = {
            "asset_id": finding.get("asset_id"), "label": _device_label(finding),
            "version": finding.get("software_version"),
        }
        cve = group["cves"].setdefault(finding["cve_id"], {
            "cve_id": finding["cve_id"], "score": float(finding["cvss_score"]) if finding.get("cvss_score") is not None else None,
            "severity": finding.get("severity") or "none", "summary": finding.get("summary") or "",
            "kev": finding.get("kev_added") is not None, "kev_due": finding.get("kev_due"),
            "ransomware": bool(finding.get("known_ransomware")), "kev_action": finding.get("kev_action"),
            "devices": set(), "status": finding["status"],
        })
        cve["devices"].add(_device_label(finding))
        if finding["status"] == "open":
            cve["status"] = "open"
    result = []
    for key, group in groups.items():
        cves = sorted(group["cves"].values(), key=lambda item: (not item["kev"], -(item["score"] or 0), item["cve_id"]))
        open_cves = [cve for cve in cves if cve["status"] == "open"]
        counts = {severity: sum(1 for cve in open_cves if cve["severity"] == severity) for severity in SEVERITIES}
        matched_ranges = [item for item in ranges.get(key, []) if item["cve_id"] in group["cves"]
                          and any(version_affected(version, item) for version in group["versions"])]
        result.append({
            "vendor": key[0], "product": key[1],
            "name": sorted(group["names"], key=len)[0],
            "versions": sorted(group["versions"], key=version_key),
            "devices": sorted(group["devices"].values(), key=lambda item: item["label"].casefold()),
            "cves": [{**cve, "devices": sorted(cve["devices"])} for cve in cves],
            "open_count": len(open_cves),
            "accepted_count": len(cves) - len(open_cves),
            "kev_count": sum(1 for cve in open_cves if cve["kev"]),
            "ransomware": any(cve["ransomware"] for cve in open_cves),
            "max_score": max((cve["score"] or 0 for cve in open_cves), default=0),
            "counts": counts,
            "worst": next((severity for severity in SEVERITIES if counts[severity]), "none"),
            "fix": fix_hint(matched_ranges),
        })
    result.sort(key=lambda item: (item["open_count"] == 0, -item["kev_count"], -item["max_score"], item["name"].casefold()))
    return result


def summarise(groups: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {severity: 0 for severity in SEVERITIES}
    devices: set[Any] = set()
    kev = 0
    for group in groups:
        for severity, value in group["counts"].items():
            counts[severity] += value
        kev += group["kev_count"]
        if group["open_count"]:
            devices.update(device["asset_id"] or device["label"] for device in group["devices"])
    return {"counts": counts, "kev": kev, "devices": len(devices),
            "software": sum(1 for group in groups if group["open_count"])}


async def company_report(company_id: int) -> dict[str, Any]:
    findings = await repo.list_findings(company_id=company_id)
    ranges = await repo.list_product_ranges({(item["vendor"], item["product"]) for item in findings})
    groups = group_by_software(findings, ranges)
    return {"groups": groups, "summary": summarise(groups),
            "exceptions": await repo.list_exceptions(company_id), "state": await repo.get_state()}


async def asset_report(asset_id: int) -> dict[str, Any]:
    findings = await repo.list_findings(asset_id=asset_id)
    ranges = await repo.list_product_ranges({(item["vendor"], item["product"]) for item in findings})
    groups = group_by_software(findings, ranges)
    return {"groups": groups, "summary": summarise(groups)}


def parse_exception_form(form: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    errors: dict[str, str] = {}
    vendor = str(form.get("vendor") or "").strip()
    product = str(form.get("product") or "").strip()
    if not _CPE_PART.match(vendor) or not _CPE_PART.match(product):
        errors["product"] = "Choose the software."
    cve_id = str(form.get("cve_id") or "").strip().upper() or None
    if cve_id and not _CVE_ID.match(cve_id):
        errors["cve_id"] = "That is not a CVE ID."
    reason = str(form.get("reason") or "").strip()[:MAX_REASON_LENGTH]
    if len(reason) < 5:
        errors["reason"] = "Say why the risk is accepted, for example the app is isolated or being replaced."
    days = str(form.get("expires_days") or "").strip()
    expires_at = None
    if days:
        if not days.isdigit() or not 1 <= int(days) <= 730:
            errors["expires_days"] = "Choose up to two years, or leave it blank to keep it until removed."
        else:
            expires_at = (now or _utcnow()) + timedelta(days=int(days))
    if errors:
        raise ValueError(errors)
    return {"vendor": vendor, "product": product, "cve_id": cve_id, "reason": reason, "expires_at": expires_at}


def ticket_description(group: Mapping[str, Any], company_name: str) -> str:
    import html

    lines = [f"<p><strong>{html.escape(group['name'])}</strong> on {len(group['devices'])} "
             f"device{'s' if len(group['devices']) != 1 else ''} at {html.escape(company_name)} has "
             f"{group['open_count']} known vulnerabilit{'ies' if group['open_count'] != 1 else 'y'}"
             f"{', ' + str(group['kev_count']) + ' actively exploited' if group['kev_count'] else ''}.</p>"]
    if group.get("fix"):
        lines.append(f"<p><strong>Fix:</strong> {html.escape(group['fix'])}.</p>")
    lines.append("<p><strong>Devices:</strong></p><ul>" + "".join(
        f"<li>{html.escape(device['label'])}{' (' + html.escape(str(device['version'])) + ')' if device.get('version') else ''}</li>"
        for device in group["devices"]) + "</ul>")
    worst = [cve for cve in group["cves"] if cve["status"] == "open"][:10]
    lines.append("<p><strong>Worst CVEs:</strong></p><ul>" + "".join(
        f"<li>{html.escape(cve['cve_id'])} ({SEVERITY_LABELS.get(cve['severity'], cve['severity'])}"
        f"{', ' + str(cve['score']) if cve['score'] is not None else ''}{', actively exploited' if cve['kev'] else ''})</li>"
        for cve in worst) + "</ul>")
    return "".join(lines)
