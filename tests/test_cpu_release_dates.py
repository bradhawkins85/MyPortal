"""CPU launch-date lookup used to estimate each device's age."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from app.services import cpu_release_dates as service


@pytest.mark.parametrize(
    ("name", "vendor", "model"),
    [
        ("Intel(R) Core(TM) i7-8650U CPU @ 1.90GHz", "intel", "i7-8650U"),
        ("13th Gen Intel(R) Core(TM) i7-1355U", "intel", "i7-1355U"),
        ("Intel(R) Core(TM) Ultra 7 155H", "intel", "Ultra 7 155H"),
        ("Intel(R) Xeon(R) CPU E5-2670 v3 @ 2.30GHz", "intel", "E5-2670 v3"),
        ("Intel(R) Xeon(R) Silver 4210 CPU @ 2.20GHz", "intel", "Silver 4210"),
        ("Intel(R) Xeon(R) E-2236 CPU @ 3.40GHz", "intel", "E-2236"),
        ("Intel(R) Xeon(R) W-2245 CPU @ 3.90GHz", "intel", "W-2245"),
        ("Intel(R) Pentium(R) CPU G4560 @ 3.50GHz", "intel", "G4560"),
        ("Intel(R) Core(TM)2 Duo CPU     E8400  @ 3.00GHz", "intel", "E8400"),
        ("Intel(R) Core(TM) i7 CPU         920  @ 2.67GHz", "intel", "i7-920"),
        ("Intel(R) Core(TM) i3-N305", "intel", "i3-N305"),
        ("Intel(R) N100", "intel", "N100"),
        ("Intel(R) Celeron(R) N4020 CPU @ 1.10GHz", "intel", "N4020"),
        ("Intel(R) Pentium(R) Silver N5000 CPU @ 1.10GHz", "intel", "N5000"),
        ("Intel Core Processor (Broadwell)", "intel", None),
        ("AMD Ryzen 7 5800X 8-Core Processor", "amd", "Ryzen 7 5800X"),
        ("AMD Ryzen 5 PRO 4650U with Radeon Graphics", "amd", "Ryzen 5 PRO 4650U"),
        ("Apple M1 Pro", "apple", "M1 Pro"),
    ],
)
def test_identify_extracts_vendor_and_model(name, vendor, model):
    identity = service.identify(name)

    assert identity is not None
    assert identity.vendor == vendor
    assert identity.model == model


def test_identify_groups_spellings_of_the_same_model():
    first = service.identify("Intel(R) Core(TM) i7-8650U CPU @ 1.90GHz")
    second = service.identify("Intel Core i7-8650U")

    assert first.key == second.key == "intel:i7-8650u"
    assert service.identify("") is None
    assert service.identify(None) is None


@pytest.mark.parametrize(
    ("text", "expected", "label"),
    [
        ("Q3'17", date(2017, 7, 1), "Q3 2017"),
        ("Q1 2020", date(2020, 1, 1), "Q1 2020"),
        ("Q4’08", date(2008, 10, 1), "Q4 2008"),
        ("04/2016", date(2016, 4, 1), "Apr 2016"),
        ("2015", date(2015, 1, 1), "2015"),
    ],
)
def test_parse_release_text(text, expected, label):
    assert service.parse_release_text(text) == (expected, label)


ARK_PRODUCT_PAGE = """
<div class="tech-section-row">
  <div class="tech-label"><span>Lithography</span></div>
  <div class="tech-data" data-key="Lithography"><span>14 nm</span></div>
</div>
<div class="tech-section-row">
  <div class="tech-label"><span>Launch Date</span></div>
  <div class="tech-data" data-key="BornOnDate"><span>Q3'17</span></div>
</div>
"""

ARK_SEARCH_RESULTS = """
<a href="/content/www/us/en/ark/products/124967/intel-core-i58350u-processor-6m-cache-up-to-3-60-ghz.html">i5-8350U</a>
<a href="/content/www/us/en/ark/products/124968/intel-core-i78650u-processor-8m-cache-up-to-4-20-ghz.html">i7-8650U</a>
"""


def test_parse_ark_launch_date_reads_born_on_date():
    assert service.parse_ark_launch_date(ARK_PRODUCT_PAGE) == (date(2017, 7, 1), "Q3 2017")


def test_parse_ark_launch_date_falls_back_to_label():
    page = '<li><span class="label">Launch Date</span><span class="value">Q2\'13</span></li>'

    assert service.parse_ark_launch_date(page) == (date(2013, 4, 1), "Q2 2013")
    assert service.parse_ark_launch_date("<p>No dates here</p>") is None


def test_find_ark_product_url_picks_matching_model():
    url = service.find_ark_product_url(ARK_SEARCH_RESULTS, "i7-8650U")

    assert url == (
        "https://ark.intel.com/content/www/us/en/ark/products/124968/"
        "intel-core-i78650u-processor-8m-cache-up-to-4-20-ghz.html"
    )
    assert service.find_ark_product_url(ARK_SEARCH_RESULTS, "i9-9900K") is None


def test_age_in_years_rounds_to_nearest_year():
    assert service.age_in_years(date(2017, 7, 1), date(2026, 11, 11)) == 9
    assert service.age_in_years(date(2020, 1, 1), date(2026, 11, 11)) == 7
    assert service.age_in_years(date(2030, 1, 1), date(2026, 11, 11)) == 0


def _session_with(handler) -> service._ArkSession:
    session = service._ArkSession()
    session._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    return session


@pytest.mark.asyncio
async def test_intel_lookup_follows_search_results_to_product_page(monkeypatch):
    monkeypatch.setattr(service, "_REQUEST_INTERVAL_SECONDS", 0)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if "search.html" in request.url.path:
            assert request.url.params.get("q") == "i7-8650U"
            return httpx.Response(200, text=ARK_SEARCH_RESULTS)
        return httpx.Response(200, text=ARK_PRODUCT_PAGE)

    session = _session_with(handler)
    try:
        result = await session.lookup(service.identify("Intel(R) Core(TM) i7-8650U CPU @ 1.90GHz"))
    finally:
        await session._client.aclose()

    assert result.status == "found"
    assert result.source == "intel_ark"
    assert result.release_date == date(2017, 7, 1)
    assert result.release_label == "Q3 2017"
    assert "/124968/" in result.source_url
    assert len(requested) == 2
    assert all(url.startswith("https://ark.intel.com/") for url in requested)


@pytest.mark.asyncio
async def test_intel_lookup_uses_redirected_product_page(monkeypatch):
    monkeypatch.setattr(service, "_REQUEST_INTERVAL_SECONDS", 0)
    session = _session_with(lambda request: httpx.Response(200, text=ARK_PRODUCT_PAGE))
    try:
        result = await session.lookup(service.identify("Intel(R) Xeon(R) CPU E5-2670 v3 @ 2.30GHz"))
    finally:
        await session._client.aclose()

    assert result.status == "found"
    assert result.release_date == date(2017, 7, 1)


@pytest.mark.asyncio
async def test_intel_lookup_reports_not_found_and_errors(monkeypatch):
    monkeypatch.setattr(service, "_REQUEST_INTERVAL_SECONDS", 0)
    session = _session_with(lambda request: httpx.Response(200, text="<p>No products matching</p>"))
    try:
        missing = await session.lookup(service.identify("Intel(R) Core(TM) i7-8650U"))
    finally:
        await session._client.aclose()
    session = _session_with(lambda request: httpx.Response(403, text="denied"))
    try:
        denied = await session.lookup(service.identify("Intel(R) Core(TM) i7-8650U"))
    finally:
        await session._client.aclose()

    assert missing.status == "not_found"
    assert denied.status == "error"
    assert "access denied" in denied.message


@pytest.mark.asyncio
async def test_non_intel_and_virtual_cpus_are_not_sent_anywhere():
    def handler(request):  # pragma: no cover - must not be called
        raise AssertionError("no request expected")

    session = _session_with(handler)
    try:
        amd = await session.lookup(service.identify("AMD Ryzen 7 5800X 8-Core Processor"))
        virtual = await session.lookup(service.identify("Intel Core Processor (Broadwell)"))
    finally:
        await session._client.aclose()

    assert amd.status == "unsupported"
    assert virtual.status == "unsupported"


@pytest.mark.asyncio
async def test_apply_to_assets_stores_release_date_and_approx_age(monkeypatch):
    saved: list[tuple[int, date | None, int | None]] = []

    async def fake_list_all():
        return [{"cpu_key": "intel:i7-8650u", "release_date": date(2017, 7, 1)},
                {"cpu_key": "amd:ryzen 7 5800x", "release_date": None}]

    async def fake_set(asset_id, *, release_date, approx_age):
        saved.append((asset_id, release_date, approx_age))

    monkeypatch.setattr(service.repo, "list_all", fake_list_all)
    monkeypatch.setattr(service.repo, "set_asset_cpu_age", fake_set)
    assets = [
        {"id": 1, "cpu_name": "Intel(R) Core(TM) i7-8650U CPU @ 1.90GHz", "cpu_release_date": None, "approx_age": 4},
        # Native RMM agents report the CPU through the inventory snapshot.
        {"id": 2, "cpu_name": None, "cpu_model": "Intel Core i7-8650U", "cpu_release_date": None, "approx_age": None},
        # Already current: nothing to write.
        {"id": 3, "cpu_name": "Intel Core i7-8650U", "cpu_release_date": date(2017, 7, 1), "approx_age": 9},
        {"id": 4, "cpu_name": "AMD Ryzen 7 5800X 8-Core Processor", "cpu_release_date": None, "approx_age": 3},
        # The processor's date was cleared: drop the stale launch date.
        {"id": 5, "cpu_name": "AMD Ryzen 7 5800X", "cpu_release_date": date(2021, 1, 1), "approx_age": 5},
    ]

    changed = await service.apply_to_assets(assets, today=date(2026, 11, 11))

    assert changed == 3
    assert saved == [
        (1, date(2017, 7, 1), 9),
        (2, date(2017, 7, 1), 9),
        (5, None, None),
    ]


@pytest.mark.asyncio
async def test_refresh_skips_when_module_disabled(monkeypatch):
    async def disabled():
        return False

    monkeypatch.setattr(service, "is_module_enabled", disabled)

    assert (await service.refresh())["status"] == "skipped"


def test_migration_creates_cache_table_and_asset_column():
    sql = (Path(__file__).parent.parent / "migrations" / "555_cpu_release_dates.sql").read_text()

    assert "-- phase: expand" in sql
    assert "CREATE TABLE IF NOT EXISTS cpu_release_dates" in sql
    assert "UNIQUE (cpu_key)" in sql
    assert "ALTER TABLE assets ADD COLUMN IF NOT EXISTS cpu_release_date DATE NULL" in sql


def test_cpu_age_lookup_module_is_registered():
    from app.core import module_capabilities
    from app.services import modules

    assert any(m["slug"] == "cpu-age-lookup" for m in modules.DEFAULT_MODULES)
    assert "cpu-age-lookup" in modules._NON_TRIGGERABLE_MODULE_SLUGS
    assert module_capabilities.modules_for_command("lookup_cpu_release_dates") == {"cpu-age-lookup"}
    assert not module_capabilities.validate_capability_registry(modules.DEFAULT_MODULES)


@pytest.mark.asyncio
async def test_integration_sync_keeps_cpu_derived_age(monkeypatch):
    """A sync must not overwrite an age estimated from the CPU launch date."""
    import sqlite3

    from app.repositories import assets as asset_repo

    executed: list[tuple[str, tuple]] = []

    async def fake_fetch_one(sql, params=None):
        return {"id": 7} if "syncro_asset_id" in sql else None

    async def fake_execute(sql, params=None):
        executed.append((sql, params))

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(asset_repo.db, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(asset_repo.db, "execute", fake_execute)
    monkeypatch.setattr(asset_repo, "_refresh_auto_tags", noop)

    await asset_repo.upsert_asset(company_id=1, name="PC-1", syncro_asset_id="55", approx_age=3)
    update, params = next((q, p) for q, p in executed if q.lstrip().startswith("UPDATE assets"))

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE assets (id INTEGER PRIMARY KEY, name, type, asset_type, asset_type_source, "
        "machine_type, status, os_name, cpu_name, ram_gb, hdd_size, last_sync, boot_time, "
        "motherboard_manufacturer, form_factor, last_user, approx_age, performance_score, "
        "warranty_status, warranty_end_date, warranty_source, syncro_asset_id, tactical_asset_id, "
        "serial_number, mac_address, cpu_release_date)"
    )
    conn.execute("INSERT INTO assets (id, approx_age, cpu_release_date) "
                 "VALUES (7, 9, '2017-07-01'), (8, 1, NULL)")
    conn.execute(update.replace("%s", "?"), params)
    conn.execute(update.replace("%s", "?"), params[:-1] + (8,))
    rows = conn.execute("SELECT id, approx_age FROM assets ORDER BY id").fetchall()
    # The CPU-derived age stays; assets without a CPU date still take the synced age.
    assert rows == [(7, 9), (8, 3.0)]


@pytest.mark.asyncio
async def test_api_manual_date_rejects_future_and_updates_assets(monkeypatch):
    from app.api.routes import cpu_release_dates as api

    entry = {"id": 3, "cpu_key": "amd:ryzen 7 5800x", "cpu_name": "AMD Ryzen 7 5800X",
             "vendor": "amd", "model": "Ryzen 7 5800X", "release_date": None, "source": None}
    calls: list[tuple[int, date | None]] = []
    audits: list[dict] = []

    async def fake_get(entry_id):
        return dict(entry) if entry_id == 3 else None

    async def fake_set(entry_id, release_date):
        calls.append((entry_id, release_date))
        return {**entry, "release_date": release_date, "source": "manual", "release_label": "Jan 2021"}

    async def fake_audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(api.cpu_repo, "get", fake_get)
    monkeypatch.setattr(api.cpu_service, "set_manual_release_date", fake_set)
    monkeypatch.setattr(api.audit_service, "record", fake_audit)
    user = {"id": 1}

    with pytest.raises(api.HTTPException) as missing:
        await api.update_cpu_release_date(99, api.CpuReleaseDateUpdate(release_date=None), None, user)
    with pytest.raises(api.HTTPException) as future:
        await api.update_cpu_release_date(3, api.CpuReleaseDateUpdate(release_date=date(2999, 1, 1)), None, user)
    saved = await api.update_cpu_release_date(3, api.CpuReleaseDateUpdate(release_date=date(2021, 1, 5)), None, user)

    assert missing.value.status_code == 404
    assert future.value.status_code == 422
    assert calls == [(3, date(2021, 1, 5))]
    assert saved.source == "manual"
    assert audits[0]["action"] == "cpu_release_date.update"


# ---------------------------------------------------------------------------
# Admin page: Assets -> CPU launch dates
# ---------------------------------------------------------------------------


def _form_request(path: str, body: bytes = b""):
    from starlette.requests import Request

    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": path,
                    "headers": [(b"content-type", b"application/x-www-form-urlencoded")]}, receive)


def _patch_cpu_routes(monkeypatch, entry):
    from unittest.mock import AsyncMock

    from starlette.responses import Response

    from app.features.assets import routes

    flashes: list[tuple[str, str, str]] = []
    main = routes._main()
    monkeypatch.setattr(main, "_require_super_admin_page", AsyncMock(return_value=({"id": 8}, None)))
    monkeypatch.setattr(service.repo, "get", AsyncMock(return_value=entry))
    saved = AsyncMock(return_value=entry)
    monkeypatch.setattr(service, "set_manual_release_date", saved)
    monkeypatch.setattr(routes.audit_service, "record", AsyncMock())

    def flash(url, message, level):
        flashes.append((url, message, level))
        return Response(status_code=303)

    monkeypatch.setattr(main, "flash_redirect", flash)
    return routes, flashes, saved


_ENTRY = {"id": 3, "cpu_key": "amd:ryzen 7 5800x", "cpu_name": "AMD Ryzen 7 5800X 8-Core Processor",
          "model": "Ryzen 7 5800X", "release_date": None, "source": None}


@pytest.mark.asyncio
async def test_admin_page_saves_month_as_first_of_month(monkeypatch):
    routes, flashes, saved = _patch_cpu_routes(monkeypatch, dict(_ENTRY))

    await routes.save_cpu_launch_date(_form_request("/assets/cpu-launch-dates/3", b"release_month=2020-11"), 3)

    saved.assert_awaited_once_with(3, date(2020, 11, 1))
    assert flashes[0][2] == "success"
    assert "Ryzen 7 5800X launched November 2020" in flashes[0][1]
    audit = routes.audit_service.record.await_args.kwargs
    assert audit["action"] == "cpu_release_date.update"
    assert audit["after"] == {"release_date": "2020-11-01", "source": "manual"}


@pytest.mark.asyncio
async def test_admin_page_clears_manual_date(monkeypatch):
    entry = {**_ENTRY, "release_date": date(2020, 11, 1), "source": "manual"}
    routes, flashes, saved = _patch_cpu_routes(monkeypatch, entry)

    await routes.save_cpu_launch_date(
        _form_request("/assets/cpu-launch-dates/3", b"release_month=2020-11&action=clear"), 3,
    )

    saved.assert_awaited_once_with(3, None)
    assert "Cleared" in flashes[0][1]


@pytest.mark.asyncio
async def test_admin_page_rejects_missing_future_and_bad_dates(monkeypatch):
    routes, flashes, saved = _patch_cpu_routes(monkeypatch, dict(_ENTRY))

    await routes.save_cpu_launch_date(_form_request("/x", b"release_month="), 3)
    await routes.save_cpu_launch_date(_form_request("/x", b"release_month=2999-01"), 3)
    with pytest.raises(routes.HTTPException) as bad:
        await routes.save_cpu_launch_date(_form_request("/x", b"release_month=last+year"), 3)

    saved.assert_not_awaited()
    assert [level for _url, _msg, level in flashes] == ["error", "error"]
    assert bad.value.status_code == 422


@pytest.mark.asyncio
async def test_admin_page_unknown_processor_is_404(monkeypatch):
    routes, _flashes, _saved = _patch_cpu_routes(monkeypatch, None)

    with pytest.raises(routes.HTTPException) as missing:
        await routes.save_cpu_launch_date(_form_request("/x", b"release_month=2020-01"), 99)

    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_admin_refresh_needs_module(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    routes, flashes, _saved = _patch_cpu_routes(monkeypatch, dict(_ENTRY))
    monkeypatch.setattr(service, "is_module_enabled", AsyncMock(return_value=False))
    background = MagicMock()

    await routes.refresh_cpu_launch_dates(_form_request("/x"), background)

    background.add_task.assert_not_called()
    assert flashes[0][2] == "error"

    monkeypatch.setattr(service, "is_module_enabled", AsyncMock(return_value=True))
    await routes.refresh_cpu_launch_dates(_form_request("/x"), background)

    background.add_task.assert_called_once()
    assert flashes[1][2] == "success"


@pytest.mark.asyncio
async def test_overview_counts_devices_per_processor(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(service.repo, "list_asset_cpus", AsyncMock(return_value=[
        {"id": 1, "cpu_name": "Intel(R) Core(TM) i7-8650U CPU @ 1.90GHz"},
        {"id": 2, "cpu_name": None, "cpu_model": "Intel Core i7-8650U"},
        {"id": 3, "cpu_name": "AMD Ryzen 7 5800X 8-Core Processor"},
    ]))
    monkeypatch.setattr(service.repo, "ensure", AsyncMock())
    monkeypatch.setattr(service.repo, "list_all", AsyncMock(return_value=[
        {"id": 1, "cpu_key": "intel:i7-8650u", "cpu_name": "Intel Core i7-8650U", "vendor": "intel",
         "release_date": date(2017, 7, 1), "source": "intel_ark"},
        {"id": 2, "cpu_key": "amd:ryzen 7 5800x", "cpu_name": "AMD Ryzen 7 5800X", "vendor": "amd",
         "release_date": None, "source": None},
    ]))

    rows = {row["cpu_key"]: row for row in await service.overview(today=date(2026, 11, 11))}

    assert service.repo.ensure.await_count == 2
    assert rows["intel:i7-8650u"]["device_count"] == 2
    assert rows["intel:i7-8650u"]["age_years"] == 9
    assert rows["intel:i7-8650u"]["source_label"] == "Intel ARK"
    assert rows["amd:ryzen 7 5800x"]["device_count"] == 1
    assert rows["amd:ryzen 7 5800x"]["age_years"] is None
    assert rows["amd:ryzen 7 5800x"]["vendor_label"] == "AMD"


def test_admin_page_template_renders():
    from app.main import templates

    processors = [
        {"id": 1, "cpu_key": "intel:i7-8650u", "cpu_name": "Intel(R) Core(TM) i7-8650U CPU @ 1.90GHz",
         "model": "i7-8650U", "vendor_label": "Intel", "release_date": date(2017, 7, 1),
         "release_label": "Q3 2017", "source": "intel_ark", "source_label": "Intel ARK",
         "source_url": "https://ark.intel.com/x.html", "device_count": 2, "age_years": 9,
         "lookup_status": "found", "lookup_message": None},
        {"id": 2, "cpu_key": "amd:ryzen 7 5800x", "cpu_name": "AMD Ryzen 7 5800X", "model": "Ryzen 7 5800X",
         "vendor_label": "AMD", "release_date": None, "release_label": None, "source": None,
         "source_label": None, "source_url": None, "device_count": 1, "age_years": None,
         "lookup_status": "unsupported", "lookup_message": "AMD has no free automated launch-date lookup."},
    ]
    template = templates.env.get_template("assets/cpu_launch_dates.html").make_module(
        {"today_iso": "2026-11-11", "processors": [], "needs_date": [], "dated": [],
         "cpu_lookup_enabled": False, "device_total": 0}
    )
    html = template.processor_item(processors[1], "2026-11")
    assert 'action="/assets/cpu-launch-dates/2"' in html
    assert "Needs a manual date" in html
    html = template.processor_item(processors[0], "2026-11")
    assert "Launched Q3 2017" in html
    assert 'data-cpu-month="2017-07"' in html
