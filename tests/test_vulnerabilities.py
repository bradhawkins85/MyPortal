"""Tests for vulnerability management (NVD CVEs and CISA KEV against RMM software)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.database import db
from app.features.rmm import PACK
from app.repositories import vulnerabilities as repo
from app.services import vulnerabilities as vulns

MIGRATION = Path("migrations/580_vulnerability_management.sql")
NOW = datetime(2026, 11, 26, 12, 0)


def _nvd_item(cve_id="CVE-2025-1234", *, criteria="cpe:2.3:a:google:chrome:*:*:*:*:*:*:*:*", vulnerable=True,
              end_excluding="120.0.6099.129", score=9.8, severity="CRITICAL", **extra):
    match = {"vulnerable": vulnerable, "criteria": criteria, "matchCriteriaId": "x"}
    if end_excluding:
        match["versionEndExcluding"] = end_excluding
    match.update(extra)
    return {"cve": {
        "id": cve_id, "published": "2025-01-02T10:15:00.000", "lastModified": "2025-02-03T11:00:00.000",
        "vulnStatus": "Analyzed",
        "descriptions": [{"lang": "es", "value": "x"}, {"lang": "en", "value": "Use after free in Blink."}],
        "metrics": {"cvssMetricV31": [{"type": "Primary", "cvssData": {"baseScore": score, "baseSeverity": severity}}]},
        "configurations": [{"nodes": [{"operator": "OR", "negate": False, "cpeMatch": [
            match,
            {"vulnerable": False, "criteria": "cpe:2.3:o:microsoft:windows:-:*:*:*:*:*:*:*"},
            {"vulnerable": True, "criteria": "cpe:2.3:a:other:thing:*:*:*:*:*:*:*:*", "versionEndExcluding": "2"},
        ]}]}],
    }}


def test_pack_registers_routes():
    paths = {route.path for router in PACK.routers for route in router.routes}
    assert {"/rmm/vulnerabilities", "/rmm/vulnerabilities/settings", "/rmm/vulnerabilities/exceptions",
            "/rmm/vulnerabilities/ticket", "/rmm/vulnerabilities/settings/sync",
            "/rmm/vulnerabilities/settings/mappings", "/api/rmm/vulnerabilities",
            "/api/rmm/assets/{asset_id}/vulnerabilities", "/api/rmm/vulnerabilities/cpe-search",
            "/api/rmm/vulnerabilities/mappings", "/api/rmm/vulnerabilities/mappings/{mapping_id}",
            "/api/rmm/vulnerabilities/sync"} <= paths


def test_migration_runs_on_sqlite():
    adapted = db._adapt_sql_for_sqlite(MIGRATION.read_text(encoding="utf-8"))
    connection = sqlite3.connect(":memory:")
    for table in ("companies", "rmm_agents", "users"):
        connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    for statement in db._split_sql_statements(adapted):
        connection.execute(statement.replace("INSERT IGNORE INTO", "INSERT OR IGNORE INTO"))
    assert connection.execute("SELECT id FROM vuln_sync_state").fetchall() == [(1,)]
    connection.execute("INSERT INTO vuln_cves (cve_id) VALUES ('CVE-2025-1')")
    connection.execute("INSERT INTO rmm_agents (id) VALUES (1)")
    connection.execute("INSERT INTO vuln_findings (agent_id, cve_id, vendor, product, software_name, first_seen_at, "
                       "last_seen_at) VALUES (1, 'CVE-2025-1', 'a', 'b', 'App', '2026-01-01', '2026-01-01')")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO vuln_findings (agent_id, cve_id, vendor, product, software_name, first_seen_at, "
                           "last_seen_at) VALUES (1, 'CVE-2025-1', 'a', 'b', 'App', '2026-01-01', '2026-01-01')")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def test_parse_nvd_cve_keeps_only_the_products_vulnerable_ranges():
    parsed = vulns.parse_nvd_cve(_nvd_item(), "google", "chrome")
    assert parsed["cve_id"] == "CVE-2025-1234"
    assert parsed["cvss_score"] == 9.8 and parsed["severity"] == "critical" and parsed["cvss_version"] == "3.1"
    assert parsed["summary"] == "Use after free in Blink."
    assert parsed["ranges"] == [{"version_exact": None, "start_including": None, "start_excluding": None,
                                 "end_including": None, "end_excluding": "120.0.6099.129"}]
    assert parsed["published_at"] == datetime(2025, 1, 2, 10, 15)


def test_parse_nvd_cve_ignores_other_products_rejected_and_unscored():
    assert vulns.parse_nvd_cve(_nvd_item(), "mozilla", "firefox") is None
    rejected = _nvd_item()
    rejected["cve"]["vulnStatus"] = "Rejected"
    assert vulns.parse_nvd_cve(rejected, "google", "chrome") is None
    unscored = _nvd_item()
    unscored["cve"]["metrics"] = {}
    assert vulns.parse_nvd_cve(unscored, "google", "chrome")["severity"] == "none"
    exact = vulns.parse_nvd_cve(_nvd_item(criteria="cpe:2.3:a:google:chrome:119.0.1:*:*:*:*:*:*:*", end_excluding=None),
                                "google", "chrome")
    assert exact["ranges"][0]["version_exact"] == "119.0.1"


def test_parse_cpe_handles_escaped_colons():
    assert vulns.parse_cpe("cpe:2.3:a:notepad-plus-plus:notepad\\+\\+:8.5:*:*:*:*:*:*:*")["product"] == "notepad\\+\\+"
    assert vulns.parse_cpe("cpe:2.3:a:vendor:pro\\:duct:1:*:*:*:*:*:*:*")["product"] == "pro\\:duct"
    assert vulns.parse_cpe("not a cpe") is None


def test_parse_kev():
    entries = vulns.parse_kev({"vulnerabilities": [
        {"cveID": "CVE-2024-0519", "vendorProject": "Google", "product": "Chromium V8", "dateAdded": "2024-01-17",
         "dueDate": "2024-02-07", "knownRansomwareCampaignUse": "Known", "requiredAction": "Apply updates."},
        {"cveID": "not-a-cve"},
    ]})
    assert entries == [{"cve_id": "CVE-2024-0519", "vendor_project": "Google", "product": "Chromium V8", "name": "",
                        "date_added": date(2024, 1, 17), "due_date": date(2024, 2, 7), "known_ransomware": True,
                        "required_action": "Apply updates."}]


@pytest.mark.parametrize(("score", "severity"), [(9.0, "critical"), (7.0, "high"), (4.0, "medium"), (0.1, "low"), (0, "none")])
def test_severity_for(score, severity):
    assert vulns.severity_for(score) == severity


# --------------------------------------------------------------------------- #
# Versions and mappings
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("version", "item", "affected"),
    [
        ("119.0.6045.200", {"end_excluding": "120.0.6099.129"}, True),
        ("120.0.6099.129", {"end_excluding": "120.0.6099.129"}, False),
        ("120.0.6099.130", {"end_excluding": "120.0.6099.129"}, False),
        ("1.10", {"start_including": "1.9", "end_including": "1.10"}, True),
        ("1.9", {"start_excluding": "1.9", "end_including": "2.0"}, False),
        ("23.01", {"version_exact": "23.01"}, True),
        ("23.1", {"version_exact": "23.01"}, True),
        ("23.02", {"version_exact": "23.01"}, False),
        ("5.0", {"version_exact": "*"}, True),
        ("5.0", {"version_exact": "-"}, False),
        ("", {"end_excluding": "9"}, False),
        ("unknown", {"end_excluding": "9"}, False),
    ],
)
def test_version_affected(version, item, affected):
    assert vulns.version_affected(version, item) is affected


def test_fix_hint_picks_the_highest_fixed_version():
    assert vulns.fix_hint([{"end_excluding": "120.0.1"}, {"end_excluding": "120.0.10"}, {"end_excluding": "119.9"}]) == (
        "Update to 120.0.10 or later")
    assert vulns.fix_hint([{"end_including": "3.0.19"}]) == "Update to a version newer than 3.0.19"
    assert vulns.fix_hint([{"version_exact": "1.0"}]) is None


def test_builtin_mappings_match_installed_names():
    mappings = list(vulns.BUILTIN_MAPPINGS)
    assert vulns.map_software("Google Chrome", mappings).product == "chrome"
    assert vulns.map_software("Mozilla Firefox (x64 en-US)", mappings).product == "firefox"
    assert vulns.map_software("7-Zip 23.01 (x64)", mappings).vendor == "7-zip"
    assert vulns.map_software("Microsoft Edge", mappings).product == "edge_chromium"
    assert vulns.map_software("Microsoft Edge WebView2 Runtime", mappings) is None
    assert vulns.map_software("Git", mappings).vendor == "git-scm"
    assert vulns.map_software("GitHub Desktop", mappings) is None


def test_custom_mappings_are_checked_first():
    custom = vulns.Mapping_("starts_with", "Google Chrome", "google", "chrome_enterprise", id=1)
    assert vulns.map_software("Google Chrome", [custom, *vulns.BUILTIN_MAPPINGS]).product == "chrome_enterprise"


def test_parse_mapping_form():
    values = vulns.parse_mapping_form({"match_type": "contains", "pattern": "  Zoom   Workplace ", "vendor": "Zoom",
                                       "product": "Workplace"})
    assert values == {"match_type": "contains", "pattern": "Zoom Workplace", "vendor": "zoom", "product": "workplace",
                      "is_enabled": True}
    from_cpe = vulns.parse_mapping_form({"pattern": "Zoom", "cpe": "cpe:2.3:a:zoom:meetings:*:*:*:*:*:*:*:*"})
    assert (from_cpe["vendor"], from_cpe["product"]) == ("zoom", "meetings")
    with pytest.raises(ValueError) as excinfo:
        vulns.parse_mapping_form({"match_type": "regex", "pattern": "x", "vendor": "a b", "product": "*"})
    assert set(excinfo.value.args[0]) == {"match_type", "pattern", "vendor", "product"}


def test_parse_exception_form():
    values = vulns.parse_exception_form({"vendor": "google", "product": "chrome", "cve_id": "cve-2025-1234",
                                         "reason": "Kiosk with no internet access", "expires_days": "90"}, now=NOW)
    assert values["cve_id"] == "CVE-2025-1234" and values["expires_at"] == NOW + timedelta(days=90)
    with pytest.raises(ValueError) as excinfo:
        vulns.parse_exception_form({"vendor": "google", "product": "chrome", "cve_id": "x", "reason": "no",
                                    "expires_days": "9999"})
    assert set(excinfo.value.args[0]) == {"cve_id", "reason", "expires_days"}


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_match_findings_opens_refreshes_accepts_and_resolves(monkeypatch):
    software = [
        {"agent_id": 1, "name": "Google Chrome", "version": "119.0.1", "company_id": 5, "asset_id": 11, "source": "machine"},
        {"agent_id": 2, "name": "Google Chrome", "version": "121.0", "company_id": 5, "asset_id": 12, "source": "machine"},
        {"agent_id": 3, "name": "Google Chrome", "version": "119.0.1", "company_id": 6, "asset_id": 13, "source": "machine"},
        {"agent_id": 1, "name": "Some Unmapped App", "version": "1.0", "company_id": 5, "asset_id": 11, "source": "machine"},
    ]
    monkeypatch.setattr(repo, "list_mappings", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "list_installed_software", AsyncMock(return_value=software))
    monkeypatch.setattr(repo, "list_product_ranges", AsyncMock(return_value={("google", "chrome"): [
        {"cve_id": "CVE-2025-1", "end_excluding": "120.0"},
        {"cve_id": "CVE-2025-2", "end_excluding": "120.0"},
    ]}))
    monkeypatch.setattr(repo, "list_exceptions", AsyncMock(return_value=[
        {"company_id": 6, "vendor": "google", "product": "chrome", "cve_id": None, "expires_at": None},
    ]))
    monkeypatch.setattr(repo, "list_finding_keys", AsyncMock(return_value={
        (1, "CVE-2025-1", "Google Chrome"): {"id": 100, "status": "open"},
        (2, "CVE-2025-1", "Google Chrome"): {"id": 101, "status": "open"},  # updated since
        (2, "CVE-2025-9", "Google Chrome"): {"id": 102, "status": "resolved"},
    }))
    inserts, refreshes, resolves = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(repo, "insert_findings", inserts)
    monkeypatch.setattr(repo, "refresh_findings", refreshes)
    monkeypatch.setattr(repo, "resolve_findings", resolves)
    monkeypatch.setattr(repo, "update_state", AsyncMock())

    result = await vulns.match_findings(NOW)

    assert result == {"new": 3, "still_open": 1, "resolved": 1}
    inserted = {(row[0], row[3], row[8]) for row in inserts.await_args.args[0]}
    assert inserted == {(1, "CVE-2025-2", "open"), (3, "CVE-2025-1", "accepted"), (3, "CVE-2025-2", "accepted")}
    assert refreshes.await_args.args[0][0][-1] == 100
    assert resolves.await_args.args[0] == [101]


@pytest.mark.asyncio
async def test_match_findings_handles_the_same_program_twice_on_one_device(monkeypatch):
    # Two installs of one program (e.g. per-user and machine-wide) share a finding key.
    software = [
        {"agent_id": 1, "name": "Google Chrome", "version": "119.0.1", "company_id": 5, "asset_id": 11, "source": "machine"},
        {"agent_id": 1, "name": "Google Chrome", "version": "118.0", "company_id": 5, "asset_id": 11, "source": "user"},
        {"agent_id": 2, "name": "Google Chrome", "version": "119.0", "company_id": 5, "asset_id": 12, "source": "machine"},
        {"agent_id": 2, "name": "Google Chrome", "version": "118.0", "company_id": 5, "asset_id": 12, "source": "user"},
    ]
    monkeypatch.setattr(repo, "list_mappings", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "list_installed_software", AsyncMock(return_value=software))
    monkeypatch.setattr(repo, "list_product_ranges", AsyncMock(return_value={("google", "chrome"): [
        {"cve_id": "CVE-2025-1", "end_excluding": "120.0"},
    ]}))
    monkeypatch.setattr(repo, "list_exceptions", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "list_finding_keys", AsyncMock(return_value={
        (2, "CVE-2025-1", "Google Chrome"): {"id": 100, "status": "open"},
    }))
    inserts, refreshes, resolves = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(repo, "insert_findings", inserts)
    monkeypatch.setattr(repo, "refresh_findings", refreshes)
    monkeypatch.setattr(repo, "resolve_findings", resolves)
    monkeypatch.setattr(repo, "update_state", AsyncMock())

    result = await vulns.match_findings(NOW)

    assert result == {"new": 1, "still_open": 1, "resolved": 0}
    assert [(row[0], row[3]) for row in inserts.await_args.args[0]] == [(1, "CVE-2025-1")]
    assert [row[-1] for row in refreshes.await_args.args[0]] == [100]
    assert resolves.await_args.args[0] == []


@pytest.mark.asyncio
async def test_distribution_packages_are_left_out(monkeypatch):
    lister = AsyncMock(return_value=[])
    monkeypatch.setattr(repo, "list_installed_software", lister)
    await vulns.installed_products(list(vulns.BUILTIN_MAPPINGS))
    assert lister.await_args.args[0] == ("dpkg", "rpm", "pacman")


def test_expired_exception_no_longer_covers():
    exception = {"company_id": 5, "vendor": "a", "product": "b", "cve_id": "CVE-2025-1", "expires_at": NOW - timedelta(days=1)}
    assert not vulns._exception_covers(exception, company_id=5, vendor="a", product="b", cve_id="CVE-2025-1", now=NOW)
    exception["expires_at"] = None
    assert vulns._exception_covers(exception, company_id=5, vendor="a", product="b", cve_id="CVE-2025-1", now=NOW)
    assert not vulns._exception_covers(exception, company_id=5, vendor="a", product="b", cve_id="CVE-2025-2", now=NOW)
    assert not vulns._exception_covers(exception, company_id=6, vendor="a", product="b", cve_id="CVE-2025-1", now=NOW)


def _finding(**overrides):
    base = {"agent_id": 1, "asset_id": 11, "asset_name": "PC-01", "vendor": "google", "product": "chrome",
            "software_name": "Google Chrome", "software_version": "119.0.1", "cve_id": "CVE-2025-1", "cvss_score": 9.8,
            "severity": "critical", "summary": "Bad", "kev_added": None, "kev_due": None, "known_ransomware": 0,
            "kev_action": None, "status": "open"}
    base.update(overrides)
    return base


def test_group_by_software_puts_exploited_first_and_suggests_the_fix():
    findings = [
        _finding(),
        _finding(cve_id="CVE-2025-2", cvss_score=5.0, severity="medium", kev_added=date(2025, 1, 1), known_ransomware=1),
        _finding(agent_id=2, asset_id=12, asset_name="PC-02", software_version="119.0.2"),
        _finding(vendor="7-zip", product="7-zip", software_name="7-Zip 23.01 (x64)", software_version="23.01",
                 cve_id="CVE-2025-3", cvss_score=7.8, severity="high", status="accepted"),
    ]
    ranges = {("google", "chrome"): [{"cve_id": "CVE-2025-1", "end_excluding": "120.0.1"},
                                     {"cve_id": "CVE-2025-2", "end_excluding": "120.0.9"}],
              ("7-zip", "7-zip"): [{"cve_id": "CVE-2025-3", "end_excluding": "24.07"}]}
    groups = vulns.group_by_software(findings, ranges)
    chrome, zip_ = groups
    assert chrome["name"] == "Google Chrome" and chrome["open_count"] == 2 and chrome["kev_count"] == 1
    assert chrome["cves"][0]["cve_id"] == "CVE-2025-2"  # actively exploited first
    assert chrome["worst"] == "critical" and chrome["ransomware"]
    assert chrome["fix"] == "Update to 120.0.9 or later"
    assert [device["label"] for device in chrome["devices"]] == ["PC-01", "PC-02"]
    assert zip_["open_count"] == 0 and zip_["accepted_count"] == 1
    summary = vulns.summarise(groups)
    assert summary == {"counts": {"critical": 1, "high": 0, "medium": 1, "low": 0, "none": 0}, "kev": 1,
                       "devices": 2, "software": 1}


def test_ticket_description_escapes_names():
    group = vulns.group_by_software([_finding(software_name="<b>App</b>", asset_name="<i>PC</i>")],
                                    {("google", "chrome"): [{"cve_id": "CVE-2025-1", "end_excluding": "120"}]})[0]
    text = vulns.ticket_description(group, "Acme & Co")
    assert "&lt;b&gt;App&lt;/b&gt;" in text and "<b>App</b>" not in text and "Acme &amp; Co" in text
    assert "Update to 120 or later" in text and "CVE-2025-1" in text


# --------------------------------------------------------------------------- #
# Syncing
# --------------------------------------------------------------------------- #


def _transport(pages: dict[str, list[dict]], kev: dict, calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "www.cisa.gov":
            return httpx.Response(200, json=kev)
        match = request.url.params.get("virtualMatchString")
        start = int(request.url.params.get("startIndex") or 0)
        items = pages.get(match, [])
        page = items[start:start + 1]
        return httpx.Response(200, json={"resultsPerPage": len(page), "startIndex": start, "totalResults": len(items),
                                         "vulnerabilities": page})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_sync_downloads_kev_and_each_products_cves(monkeypatch):
    calls: list[httpx.Request] = []
    pages = {"cpe:2.3:a:google:chrome": [_nvd_item("CVE-2025-1001"), _nvd_item("CVE-2025-1002")]}
    transport = _transport(pages, {"vulnerabilities": [{"cveID": "CVE-2025-1001", "dateAdded": "2025-01-01"}]}, calls)
    monkeypatch.setattr(vulns, "monitored_client", lambda _cls, **kwargs: httpx.AsyncClient(transport=transport))
    monkeypatch.setattr(repo, "get_state", AsyncMock(return_value={}))
    monkeypatch.setattr(repo, "list_products", AsyncMock(return_value={}))
    monkeypatch.setattr(repo, "list_mappings", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "list_installed_software", AsyncMock(return_value=[
        {"agent_id": 1, "name": "Google Chrome", "version": "119", "company_id": 5, "asset_id": 1}]))
    replace_kev, save_cves, save_product = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(repo, "replace_kev", replace_kev)
    monkeypatch.setattr(repo, "save_cves", save_cves)
    monkeypatch.setattr(repo, "save_product", save_product)
    monkeypatch.setattr(repo, "update_state", AsyncMock())
    monkeypatch.setattr(vulns, "match_findings", AsyncMock(return_value={"new": 2}))

    result = await vulns.sync(now=NOW, sleep=AsyncMock())

    assert result["kev"] == 1 and result["products"] == 1 and result["cves"] == 2 and result["errors"] == []
    assert [cve["cve_id"] for cve in save_cves.await_args.args[0]] == ["CVE-2025-1001", "CVE-2025-1002"]
    nvd_calls = [call for call in calls if call.url.host == "services.nvd.nist.gov"]
    assert len(nvd_calls) == 2  # two pages
    assert "lastModStartDate" not in nvd_calls[0].url.params  # first sync downloads everything


@pytest.mark.asyncio
async def test_sync_is_incremental_after_the_first_download(monkeypatch):
    calls: list[httpx.Request] = []
    transport = _transport({}, {"vulnerabilities": []}, calls)
    monkeypatch.setattr(vulns, "monitored_client", lambda _cls, **kwargs: httpx.AsyncClient(transport=transport))
    monkeypatch.setattr(repo, "get_state", AsyncMock(return_value={"kev_synced_at": NOW - timedelta(hours=1)}))
    monkeypatch.setattr(repo, "list_products", AsyncMock(return_value={
        ("google", "chrome"): {"synced_at": NOW - timedelta(days=2)}}))
    monkeypatch.setattr(repo, "list_mappings", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "list_installed_software", AsyncMock(return_value=[
        {"agent_id": 1, "name": "Google Chrome", "version": "119"}]))
    for name in ("save_cves", "save_product", "update_state", "replace_kev"):
        monkeypatch.setattr(repo, name, AsyncMock())
    monkeypatch.setattr(vulns, "match_findings", AsyncMock(return_value={}))
    result = await vulns.sync(now=NOW, sleep=AsyncMock())
    assert result["kev"] is None  # KEV was fresh
    assert calls[0].url.params["lastModStartDate"].startswith("2026-11-24T11:00:00")


@pytest.mark.asyncio
async def test_sync_records_nvd_errors_and_still_matches(monkeypatch):
    def handler(request):
        return httpx.Response(503) if request.url.host == "services.nvd.nist.gov" else httpx.Response(200, json={})
    monkeypatch.setattr(vulns, "monitored_client",
                        lambda _cls, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(repo, "get_state", AsyncMock(return_value={}))
    monkeypatch.setattr(repo, "list_products", AsyncMock(return_value={}))
    monkeypatch.setattr(repo, "list_mappings", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "list_installed_software", AsyncMock(return_value=[{"agent_id": 1, "name": "Git", "version": "2.4"}]))
    save_product = AsyncMock()
    monkeypatch.setattr(repo, "save_product", save_product)
    monkeypatch.setattr(repo, "update_state", AsyncMock())
    monkeypatch.setattr(repo, "replace_kev", AsyncMock())
    matcher = AsyncMock(return_value={})
    monkeypatch.setattr(vulns, "match_findings", matcher)
    result = await vulns.sync(now=NOW, sleep=AsyncMock())
    assert result["errors"] and "git-scm:git" in result["errors"][0]
    assert save_product.await_args.kwargs["synced_at"] is None
    matcher.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_runs_once_a_day_or_when_requested(monkeypatch):
    claim = AsyncMock(return_value=True)
    monkeypatch.setattr(repo, "claim_run", claim)
    started = []
    monkeypatch.setattr(vulns, "_run_in_background", AsyncMock(side_effect=lambda force: started.append(force)))
    monkeypatch.setattr(repo, "get_state", AsyncMock(return_value={"nvd_synced_at": NOW - timedelta(hours=2)}))
    assert await vulns.tick(NOW) is False
    monkeypatch.setattr(repo, "get_state", AsyncMock(return_value={"nvd_synced_at": NOW - timedelta(hours=2),
                                                                    "requested_at": NOW}))
    assert await vulns.tick(NOW) is True
    monkeypatch.setattr(repo, "get_state", AsyncMock(return_value={"nvd_synced_at": NOW - timedelta(days=2)}))
    claim.return_value = False  # another instance is running it
    assert await vulns.tick(NOW) is False


@pytest.mark.asyncio
async def test_failed_sync_backs_off_instead_of_restarting_every_minute(monkeypatch):
    claim = AsyncMock(return_value=True)
    monkeypatch.setattr(repo, "claim_run", claim)
    monkeypatch.setattr(vulns, "_run_in_background", AsyncMock())
    failed = {"nvd_synced_at": NOW - timedelta(days=2), "failed_at": NOW - timedelta(minutes=1)}
    monkeypatch.setattr(repo, "get_state", AsyncMock(return_value=failed))
    assert await vulns.tick(NOW) is False
    claim.assert_not_awaited()
    monkeypatch.setattr(repo, "get_state", AsyncMock(return_value={**failed, "requested_at": NOW}))
    assert await vulns.tick(NOW) is True  # Sync now still retries at once
    monkeypatch.setattr(repo, "get_state", AsyncMock(return_value={
        "failed_at": NOW - vulns.FAILURE_RETRY - timedelta(minutes=1)}))
    assert await vulns.tick(NOW) is True


@pytest.mark.asyncio
async def test_run_in_background_records_the_failure_and_clears_running(monkeypatch):
    monkeypatch.setattr(vulns, "sync", AsyncMock(side_effect=RuntimeError("boom")))
    update = AsyncMock()
    monkeypatch.setattr(repo, "update_state", update)
    await vulns._run_in_background(force=False)
    failure = update.await_args_list[0].kwargs
    assert failure["failed_at"] is not None and failure["last_error"] == "Sync failed: boom"
    assert update.await_args_list[-1].kwargs == {"running_since": None}


@pytest.mark.asyncio
async def test_one_product_failing_unexpectedly_does_not_stop_the_sync(monkeypatch):
    transport = _transport({}, {"vulnerabilities": []}, [])
    monkeypatch.setattr(vulns, "monitored_client", lambda _cls, **kwargs: httpx.AsyncClient(transport=transport))
    monkeypatch.setattr(repo, "get_state", AsyncMock(return_value={}))
    monkeypatch.setattr(repo, "list_products", AsyncMock(return_value={}))
    monkeypatch.setattr(repo, "list_mappings", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "list_installed_software", AsyncMock(return_value=[
        {"agent_id": 1, "name": "Git", "version": "2.4"}, {"agent_id": 1, "name": "Google Chrome", "version": "1"}]))
    monkeypatch.setattr(repo, "save_cves", AsyncMock(side_effect=[KeyError("bad"), None]))
    for name in ("save_product", "replace_kev"):
        monkeypatch.setattr(repo, name, AsyncMock())
    update = AsyncMock()
    monkeypatch.setattr(repo, "update_state", update)
    monkeypatch.setattr(vulns, "match_findings", AsyncMock(return_value={}))
    result = await vulns.sync(now=NOW, sleep=AsyncMock())
    assert len(result["errors"]) == 1 and result["products"] == 1
    assert update.await_args_list[-1].kwargs["nvd_synced_at"] == NOW
    assert update.await_args_list[-1].kwargs["failed_at"] is None


def test_failed_at_migration_runs_on_sqlite():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE vuln_sync_state (id INTEGER PRIMARY KEY)")
    sql = db._adapt_sql_for_sqlite(Path("migrations/586_vuln_sync_failed_at.sql").read_text(encoding="utf-8"))
    for statement in db._split_sql_statements(sql):
        connection.execute(statement.replace("ADD COLUMN IF NOT EXISTS", "ADD COLUMN"))
    assert "failed_at" in {row[1] for row in connection.execute("PRAGMA table_info(vuln_sync_state)")}


def test_nvd_api_key_is_sent_as_a_header(monkeypatch):
    from app.core import config

    monkeypatch.setattr(vulns, "get_settings", lambda: SimpleNamespace(nvd_api_key="abc"))
    assert vulns._nvd_headers() == {"apiKey": "abc"} and vulns.nvd_delay() == vulns.NVD_DELAY_WITH_KEY
    monkeypatch.setattr(vulns, "get_settings", lambda: SimpleNamespace(nvd_api_key=None))
    assert vulns._nvd_headers() == {} and vulns.nvd_delay() == vulns.NVD_DELAY_NO_KEY
    assert "nvd_api_key" in config.Settings.model_fields


def test_env_example_documents_the_key():
    assert "NVD_API_KEY=" in Path(".env.example").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Routes and templates
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_settings_and_mapping_routes_require_super_admin(monkeypatch):
    from fastapi.responses import RedirectResponse

    from app.features.rmm import vulnerability_routes as routes

    redirect = RedirectResponse("/login")
    main = SimpleNamespace(_require_super_admin_page=AsyncMock(return_value=(None, redirect)))
    monkeypatch.setattr(routes, "_main", lambda: main)
    create = AsyncMock()
    monkeypatch.setattr(repo, "create_mapping", create)
    assert await routes.settings_page(SimpleNamespace()) is redirect
    assert await routes.create_mapping(SimpleNamespace()) is redirect
    assert await routes.sync_now(SimpleNamespace()) is redirect
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_exception_from_another_company_is_not_found(monkeypatch):
    from fastapi import HTTPException

    from app.features.rmm import vulnerability_routes as routes

    monkeypatch.setattr(routes, "_load", AsyncMock(return_value=({"id": 1}, {}, 5, True)))
    monkeypatch.setattr(repo, "get_exception", AsyncMock(return_value={"id": 3, "company_id": 6}))
    delete = AsyncMock()
    monkeypatch.setattr(repo, "delete_exception", delete)
    with pytest.raises(HTTPException) as excinfo:
        await routes.delete_exception(3, SimpleNamespace())
    assert excinfo.value.status_code == 404
    delete.assert_not_awaited()


def test_templates_compile_and_post_with_csrf():
    import app.main as main_module

    for name in ("rmm/vulnerabilities.html", "rmm/vulnerability_settings.html", "rmm/_asset_vulnerabilities.html",
                 "rmm/_vulnerability_macros.html"):
        main_module.templates.env.get_template(name)
    for name in ("rmm/vulnerabilities.html", "rmm/vulnerability_settings.html"):
        source = Path("app/templates/" + name).read_text(encoding="utf-8")
        assert source.count('method="post"') <= source.count("csrf"), name
        assert "<dialog" not in source and "onclick=" not in source


def test_software_card_renders_and_escapes():
    import app.main as main_module

    group = vulns.group_by_software([_finding(summary="<script>x</script>", kev_added=date(2025, 1, 1))],
                                    {("google", "chrome"): [{"cve_id": "CVE-2025-1", "end_excluding": "120"}]})[0]
    module = main_module.templates.env.get_template("rmm/_vulnerability_macros.html").module
    html = str(module.software_card(group, vulns.SEVERITY_LABELS, can_edit=True, csrf_token="tok"))
    assert "&lt;script&gt;" in html and "<script>x" not in html
    assert "Actively exploited" in html and "Update to 120 or later" in html
    assert 'name="_csrf" value="tok"' in html and "https://nvd.nist.gov/vuln/detail/CVE-2025-1" in html


@pytest.mark.asyncio
async def test_repository_queries_bind_every_value(monkeypatch):
    calls = []

    def record(result):
        async def capture(sql, params=None):
            calls.append((sql, tuple(params or ())))
            return result
        return capture

    async def capture_many(sql, rows):
        for row in rows:
            calls.append((sql, tuple(row)))

    monkeypatch.setattr(db, "fetch_all", record([]))
    monkeypatch.setattr(db, "fetch_one", record(None))
    monkeypatch.setattr(db, "execute", record(None))
    monkeypatch.setattr(db, "execute_rowcount", record(1))
    monkeypatch.setattr(db, "execute_returning_lastrowid", record(1))
    monkeypatch.setattr(db, "execute_many", capture_many)
    await repo.list_installed_software(vulns.DISTRO_SOURCES)
    await repo.list_unmapped_candidates(vulns.DISTRO_SOURCES)
    await repo.list_product_ranges({("google", "chrome"), ("7-zip", "7-zip")})
    await repo.list_findings(company_id=5, asset_id=2)
    await repo.list_exceptions(5)
    await repo.set_findings_status(company_id=5, vendor="a", product="b", cve_id="CVE-2025-1",
                                   from_status="open", to_status="accepted")
    await repo.update_state(matched_at=NOW, last_error=None)
    await repo.save_cves([{"cve_id": "CVE-2025-1", "ranges": [{"end_excluding": "2"}]}], vendor="a", product="b")
    await repo.existing_cve_ids(["CVE-2025-1", "CVE-2025-2"])
    await repo.create_exception(company_id=5, vendor="a", product="b", cve_id=None, reason="r", expires_at=None, user_id=1)
    for sql, params in calls:
        assert sql.count("%s") == len(params), sql
