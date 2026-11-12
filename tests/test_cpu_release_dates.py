"""CPU launch dates used to estimate each device's age."""

from __future__ import annotations

from datetime import date
from pathlib import Path

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
        # As Intel ARK shows it, e.g. the i7-1065G7 "Launch Date" of Q3'19.
        ("Q3'19", date(2019, 7, 1), "Q3 2019"),
        ("q3 '19", date(2019, 7, 1), "Q3 2019"),
        ("Q4’08", date(2008, 10, 1), "Q4 2008"),
        ("Q1 2020", date(2020, 1, 1), "Q1 2020"),
        ("2019-07", date(2019, 7, 1), "Jul 2019"),
        ("2019-07-23", date(2019, 7, 1), "Jul 2019"),
        ("07/2019", date(2019, 7, 1), "Jul 2019"),
        ("Jul 2019", date(2019, 7, 1), "Jul 2019"),
        ("September 2021", date(2021, 9, 1), "Sep 2021"),
        ("2015", date(2015, 1, 1), "2015"),
    ],
)
def test_parse_release_text_accepts_ark_and_month_formats(text, expected, label):
    assert service.parse_release_text(text) == (expected, label)


@pytest.mark.parametrize("text", ["", None, "Q5'19", "2019-13", "13/2019", "Smarch 2019", "last year", "Q3'19 extra"])
def test_parse_release_text_rejects_other_input(text):
    assert service.parse_release_text(text) is None


def test_age_in_years_rounds_to_nearest_year():
    assert service.age_in_years(date(2019, 7, 1), date(2026, 11, 11)) == 7
    assert service.age_in_years(date(2017, 7, 1), date(2026, 11, 11)) == 9
    assert service.age_in_years(date(2030, 1, 1), date(2026, 11, 11)) == 0


def test_ark_search_url_links_intel_search_for_the_model():
    assert service.ark_search_url("i7-1065G7") == (
        "https://www.intel.com/content/www/us/en/search.html?ws=text#q=i7-1065G7"
    )
    assert service.ark_search_url("E5-2670 v3").endswith("#q=E5-2670%20v3")
    assert service.ark_search_url(None) is None


def test_clean_source_url_only_accepts_intel_https_links():
    page = ("https://www.intel.com/content/www/us/en/products/sku/196597/"
            "intel-core-i71065g7-processor-8m-cache-up-to-3-90-ghz/specifications.html")
    assert service.clean_source_url(page) == page
    assert service.clean_source_url("  ") is None
    for bad in ("http://www.intel.com/x", "https://intel.com.evil.example/x",
                "https://example.com/intel.com", "javascript:alert(1)"):
        with pytest.raises(service.LaunchDateError):
            service.clean_source_url(bad)


def test_service_makes_no_network_requests():
    """Intel blocks automated access to ARK, so the service must not fetch it."""
    source = Path(service.__file__).read_text()

    assert "httpx" not in source
    assert "monitored_client" not in source


@pytest.mark.asyncio
async def test_set_release_date_records_source_and_updates_devices(monkeypatch):
    from unittest.mock import AsyncMock

    entry = {"id": 4, "cpu_key": "intel:i7-1065g7", "model": "i7-1065G7", "release_date": None}
    monkeypatch.setattr(service.repo, "get", AsyncMock(return_value=entry))
    set_manual = AsyncMock()
    monkeypatch.setattr(service.repo, "set_manual", set_manual)
    apply = AsyncMock(return_value=2)
    monkeypatch.setattr(service, "apply_to_assets", apply)
    page = "https://www.intel.com/content/www/us/en/products/sku/196597/x/specifications.html"

    await service.set_release_date(4, (date(2019, 7, 1), "Q3 2019"), source="intel_ark", source_url=page)
    await service.set_release_date(4, None)

    assert set_manual.await_args_list[0].kwargs == {
        "release_date": date(2019, 7, 1), "release_label": "Q3 2019",
        "source": "intel_ark", "source_url": page,
    }
    assert set_manual.await_args_list[1].kwargs == {"release_date": None, "release_label": None}
    assert apply.await_count == 2
    with pytest.raises(service.LaunchDateError):
        await service.set_release_date(4, (date(2019, 7, 1), "Q3 2019"), source="guess")
    with pytest.raises(service.LaunchDateError):
        await service.set_release_date(4, (date(2019, 7, 1), "Q3 2019"), source_url="https://example.com/")


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
async def test_refresh_registers_models_and_updates_ages(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(service, "is_module_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "discover_cpus", AsyncMock(return_value=[
        {"id": 1, "_cpu": service.identify("Intel Core i7-1065G7")},
        {"id": 2, "_cpu": service.identify("Intel(R) Core(TM) i7-1065G7 CPU @ 1.30GHz")},
        {"id": 3, "_cpu": None},
    ]))
    monkeypatch.setattr(service, "apply_to_assets", AsyncMock(return_value=2))

    assert await service.refresh() == {"status": "ok", "processors": 1, "assets_updated": 2}


@pytest.mark.asyncio
async def test_api_records_ark_date_and_rejects_bad_input(monkeypatch):
    from unittest.mock import AsyncMock

    from app.api.routes import cpu_release_dates as api

    entry = {"id": 3, "cpu_key": "intel:i7-1065g7", "cpu_name": "Intel(R) Core(TM) i7-1065G7 CPU @ 1.30GHz",
             "vendor": "intel", "model": "i7-1065G7", "release_date": None, "source": None}
    monkeypatch.setattr(api.cpu_repo, "get", AsyncMock(side_effect=lambda i: dict(entry) if i == 3 else None))
    saved = AsyncMock(return_value={**entry, "release_date": date(2019, 7, 1), "release_label": "Q3 2019",
                                    "source": "intel_ark"})
    monkeypatch.setattr(api.cpu_service, "set_release_date", saved)
    monkeypatch.setattr(api.audit_service, "record", AsyncMock())
    user = {"id": 1}

    with pytest.raises(api.HTTPException) as missing:
        await api.update_cpu_release_date(99, api.CpuReleaseDateUpdate(launch_date=None), None, user)
    with pytest.raises(api.HTTPException) as unreadable:
        await api.update_cpu_release_date(3, api.CpuReleaseDateUpdate(launch_date="soon"), None, user)
    with pytest.raises(api.HTTPException) as future:
        await api.update_cpu_release_date(3, api.CpuReleaseDateUpdate(launch_date="2099-01"), None, user)
    result = await api.update_cpu_release_date(
        3, api.CpuReleaseDateUpdate(launch_date="Q3'19", source="intel_ark"), None, user,
    )

    assert missing.value.status_code == 404
    assert unreadable.value.status_code == 422
    assert future.value.status_code == 422
    assert saved.await_args_list[-1].args == (3, (date(2019, 7, 1), "Q3 2019"))
    assert saved.await_args_list[-1].kwargs == {"source": "intel_ark", "source_url": None}
    assert result.source == "intel_ark"
    assert api.audit_service.record.await_args.kwargs["action"] == "cpu_release_date.update"


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
    saved = AsyncMock(return_value={**(entry or {}), "source": "intel_ark", "source_url": None})
    monkeypatch.setattr(service, "set_release_date", saved)
    monkeypatch.setattr(routes.audit_service, "record", AsyncMock())

    def flash(url, message, level):
        flashes.append((url, message, level))
        return Response(status_code=303)

    monkeypatch.setattr(main, "flash_redirect", flash)
    return routes, flashes, saved


_ENTRY = {"id": 3, "cpu_key": "intel:i7-1065g7", "cpu_name": "Intel(R) Core(TM) i7-1065G7 CPU @ 1.30GHz",
          "vendor": "intel", "model": "i7-1065G7", "release_date": None, "source": None}


@pytest.mark.asyncio
async def test_admin_page_saves_ark_launch_date(monkeypatch):
    routes, flashes, saved = _patch_cpu_routes(monkeypatch, dict(_ENTRY))
    body = (b"launch_date=Q3%2719&source=intel_ark&source_url=https%3A%2F%2Fwww.intel.com"
            b"%2Fcontent%2Fwww%2Fus%2Fen%2Fproducts%2Fsku%2F196597%2Fx%2Fspecifications.html")

    await routes.save_cpu_launch_date(_form_request("/assets/cpu-launch-dates/3", body), 3)

    assert saved.await_args.args == (3, (date(2019, 7, 1), "Q3 2019"))
    assert saved.await_args.kwargs["source"] == "intel_ark"
    assert saved.await_args.kwargs["source_url"].endswith("/sku/196597/x/specifications.html")
    assert flashes == [("/assets/cpu-launch-dates",
                        "Saved: i7-1065G7 launched Q3 2019. Devices with it have been updated.", "success")]
    audit = routes.audit_service.record.await_args.kwargs
    assert audit["after"]["release_date"] == "2019-07-01"


@pytest.mark.asyncio
async def test_admin_page_clears_date(monkeypatch):
    entry = {**_ENTRY, "release_date": date(2019, 7, 1), "source": "intel_ark"}
    routes, flashes, saved = _patch_cpu_routes(monkeypatch, entry)

    await routes.save_cpu_launch_date(
        _form_request("/assets/cpu-launch-dates/3", b"launch_date=Q3%2719&action=clear"), 3,
    )

    assert saved.await_args.args == (3, None)
    assert "Cleared" in flashes[0][1]


@pytest.mark.asyncio
async def test_admin_page_rejects_missing_unreadable_and_future_dates(monkeypatch):
    routes, flashes, saved = _patch_cpu_routes(monkeypatch, dict(_ENTRY))

    await routes.save_cpu_launch_date(_form_request("/x", b"launch_date="), 3)
    await routes.save_cpu_launch_date(_form_request("/x", b"launch_date=last+year"), 3)
    await routes.save_cpu_launch_date(_form_request("/x", b"launch_date=2099-01"), 3)

    saved.assert_not_awaited()
    assert [level for _url, _msg, level in flashes] == ["error", "error", "error"]
    assert "Q3'19" in flashes[0][1]
    assert "last year" in flashes[1][1]
    assert "future" in flashes[2][1]


@pytest.mark.asyncio
async def test_admin_page_reports_bad_source_link(monkeypatch):
    from unittest.mock import AsyncMock

    routes, flashes, _saved = _patch_cpu_routes(monkeypatch, dict(_ENTRY))
    monkeypatch.setattr(service, "set_release_date",
                        AsyncMock(side_effect=service.LaunchDateError("Paste the https:// link")))

    await routes.save_cpu_launch_date(
        _form_request("/x", b"launch_date=Q3%2719&source_url=https%3A%2F%2Fexample.com"), 3,
    )

    assert flashes == [("/assets/cpu-launch-dates", "Paste the https:// link", "error")]
    routes.audit_service.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_page_unknown_processor_is_404(monkeypatch):
    routes, _flashes, _saved = _patch_cpu_routes(monkeypatch, None)

    with pytest.raises(routes.HTTPException) as missing:
        await routes.save_cpu_launch_date(_form_request("/x", b"launch_date=Q3%2719"), 99)

    assert missing.value.status_code == 404


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
        {"id": 1, "cpu_key": "intel:i7-8650u", "cpu_name": "Intel Core i7-8650U", "vendor": "intel", "model": "i7-8650U",
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
    assert rows["amd:ryzen 7 5800x"]["ark_url"] is None
    assert rows["intel:i7-8650u"]["ark_url"] == (
        "https://www.intel.com/content/www/us/en/search.html?ws=text#q=i7-8650U"
    )


def test_admin_page_template_renders():
    from app.main import templates

    module = templates.env.get_template("assets/cpu_launch_dates.html").make_module(
        {"today_iso": "2026-11-11", "processors": [], "needs_date": [], "dated": [],
         "cpu_lookup_enabled": False, "device_total": 0}
    )
    undated_intel = {
        "id": 5, "cpu_key": "intel:i7-1065g7", "cpu_name": "Intel(R) Core(TM) i7-1065G7 CPU @ 1.30GHz",
        "model": "i7-1065G7", "vendor": "intel", "vendor_label": "Intel", "release_date": None,
        "release_label": None, "source": None, "source_label": None, "source_url": None,
        "device_count": 3, "age_years": None,
        "ark_url": "https://www.intel.com/content/www/us/en/search.html?ws=text#q=i7-1065G7",
    }
    dated_amd = {
        "id": 2, "cpu_key": "amd:ryzen 7 5800x", "cpu_name": "AMD Ryzen 7 5800X", "model": "Ryzen 7 5800X",
        "vendor": "amd", "vendor_label": "AMD", "release_date": date(2020, 11, 1), "release_label": "Nov 2020",
        "source": "manual", "source_label": "Other source", "source_url": None, "device_count": 1,
        "age_years": 6, "ark_url": None,
    }

    html = module.processor_item(undated_intel)
    assert "Needs a date from Intel ARK" in html
    assert "Open on Intel ARK" in html
    assert 'href="https://www.intel.com/content/www/us/en/search.html?ws=text#q=i7-1065G7"' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'data-cpu-intel="1"' in html
    assert 'name="launch_date"' in html
    assert 'action="/assets/cpu-launch-dates/5"' in html

    html = module.processor_item(dated_amd)
    assert "Launched Nov 2020" in html
    assert "Other source" in html
    assert "Open on Intel ARK" not in html
    assert 'data-cpu-value="Nov 2020"' in html
