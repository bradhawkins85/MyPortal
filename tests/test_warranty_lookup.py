"""Asset warranty lookup: vendor detection, vendor response parsing and storage."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from unittest.mock import AsyncMock

import httpx
import pytest

from app.repositories import assets as asset_repo
from app.services import warranty_lookup as wl


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (("Dell Inc.",), "dell"),
        (("Alienware",), "dell"),
        (("HP",), "hp"),
        (("Hewlett-Packard",), "hp"),
        (("Hewlett Packard Enterprise",), "hpe"),
        (("LENOVO",), "lenovo"),
        (("ASUSTeK COMPUTER INC.",), "asus"),
        (("Micro-Star International Co., Ltd.",), "msi"),
        (("Gigabyte Technology Co., Ltd.",), "gigabyte"),
        (("Intel Corporation",), "intel"),
        (("Microsoft Corporation",), "microsoft"),
        ((None, "", "To Be Filled By O.E.M."), None),
        ((None, "LENOVO"), "lenovo"),
    ],
)
def test_detect_vendor(values, expected):
    assert wl.detect_vendor(*values) == expected


def test_detect_vendor_does_not_match_hp_inside_other_words():
    assert wl.detect_vendor("Shpongle Systems") is None


def test_parse_dell_entitlements_uses_latest_end_and_earliest_start():
    payload = [{
        "serviceTag": "ABC1234",
        "invalid": False,
        "shipDate": "2021-02-20T06:00:00Z",
        "entitlements": [
            {"startDate": "2021-02-21T06:00:00Z", "endDate": "2022-02-21T05:59:59.000001Z",
             "serviceLevelDescription": "Collect and Return Support"},
            {"startDate": "2021-02-21T06:00:00Z", "endDate": "2024-02-22T05:59:59Z",
             "serviceLevelDescription": "ProSupport Plus"},
        ],
    }]
    result = wl.parse_dell_entitlements(payload, "abc1234")
    assert result.status == "found"
    assert result.start_date == date(2021, 2, 21)
    assert result.end_date == date(2024, 2, 22)
    assert "ProSupport Plus" in result.message


def test_parse_dell_invalid_service_tag():
    result = wl.parse_dell_entitlements([{"serviceTag": "ZZZ", "invalid": True}], "ZZZ")
    assert result.status == "not_found"


def test_parse_hp_offers():
    payload = [{
        "sn": "5CG1234XYZ",
        "offers": [
            {"offerDescription": "HP Hardware Maintenance Onsite Support",
             "serviceObligationLineItemStartDate": "2022-05-01",
             "serviceObligationLineItemEndDate": "2025-04-30"},
        ],
    }]
    result = wl.parse_hp_warranty(payload)
    assert (result.status, result.start_date, result.end_date) == (
        "found", date(2022, 5, 1), date(2025, 4, 30)
    )


def test_parse_hp_without_dates_reports_vendor_reason():
    result = wl.parse_hp_warranty([{"sn": "X", "statusMessage": "Product number required"}])
    assert result.status == "not_found"
    assert "Product number required" in result.message


def test_parse_lenovo_support_site_combines_base_and_upgrades():
    payload = {"code": 0, "data": {
        "baseWarranties": [{"name": "Base Warranty", "startDate": "2021-06-01", "endDate": "2022-05-31"}],
        "upmaWarranties": [{"name": "Premier Support", "startDate": "2021-06-01", "endDate": "2024-05-31"}],
    }}
    result = wl.parse_lenovo_support_site(payload)
    assert (result.status, result.start_date, result.end_date) == (
        "found", date(2021, 6, 1), date(2024, 5, 31)
    )
    assert "Premier Support" in result.message


def test_parse_lenovo_support_site_unknown_serial():
    assert wl.parse_lenovo_support_site({"code": 1, "msg": "invalid"}).status == "not_found"


def test_parse_lenovo_api():
    payload = {"Serial": "PF1ABCDE", "Warranty": [
        {"Name": "Depot", "Start": "2020-01-10T00:00:00Z", "End": "2023-01-09T00:00:00Z"},
    ]}
    result = wl.parse_lenovo_api(payload)
    assert (result.start_date, result.end_date) == (date(2020, 1, 10), date(2023, 1, 9))


def _settings(**overrides):
    values = {
        "warranty_lookup_dell_client_id": None, "warranty_lookup_dell_client_secret": None,
        "warranty_lookup_hp_api_key": None, "warranty_lookup_hp_api_secret": None,
        "warranty_lookup_lenovo_client_id": None,
        "warranty_lookup_refresh_days": 30, "warranty_lookup_batch_size": 200,
    }
    values.update(overrides)
    return type("Settings", (), values)()


def _mock_vendor_http(monkeypatch, handler):
    monkeypatch.setattr(wl, "_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(
        wl, "monitored_client",
        lambda _cls, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.anyio
async def test_unsupported_vendor_points_to_vendor_site(monkeypatch):
    _mock_vendor_http(monkeypatch, lambda request: pytest.fail("no request expected"))
    async with wl._VendorSession() as session:
        result = await session.lookup("asus", "N1ABCD123")
    assert result.status == "unsupported"
    assert "https://www.asus.com/support/warranty-status-inquiry/" in result.message


@pytest.mark.anyio
async def test_dell_without_credentials_is_not_configured(monkeypatch):
    monkeypatch.setattr(wl, "get_settings", lambda: _settings())
    _mock_vendor_http(monkeypatch, lambda request: pytest.fail("no request expected"))
    async with wl._VendorSession() as session:
        result = await session.lookup("dell", "ABC1234")
    assert result.status == "not_configured"


@pytest.mark.anyio
async def test_dell_lookup_fetches_token_once_and_sends_only_the_serial(monkeypatch):
    monkeypatch.setattr(wl, "get_settings", lambda: _settings(
        warranty_lookup_dell_client_id="id", warranty_lookup_dell_client_secret="secret"))
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/token"):
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        tag = request.url.params["servicetags"]
        return httpx.Response(200, json=[{"serviceTag": tag, "entitlements": [
            {"startDate": "2023-01-01T00:00:00Z", "endDate": "2026-01-01T00:00:00Z"}]}])

    _mock_vendor_http(monkeypatch, handler)
    async with wl._VendorSession() as session:
        first = await session.lookup("dell", "ABC1234")
        second = await session.lookup("dell", "DEF5678")
    assert first.end_date == second.end_date == date(2026, 1, 1)
    assert [r.url.host for r in seen] == ["apigtwb2c.us.dell.com"] * 3
    assert sum(r.url.path.endswith("/token") for r in seen) == 1


@pytest.mark.anyio
async def test_hp_token_uses_basic_auth(monkeypatch):
    monkeypatch.setattr(wl, "get_settings", lambda: _settings(
        warranty_lookup_hp_api_key="key", warranty_lookup_hp_api_secret="secret"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            assert request.headers["Authorization"].startswith("Basic ")
            assert b"secret" not in request.content
            return httpx.Response(200, json={"access_token": "tok"})
        assert json.loads(request.content) == [{"sn": "5CG1234XYZ"}]
        return httpx.Response(200, json=[{"offers": [
            {"serviceObligationLineItemStartDate": "2022-01-01",
             "serviceObligationLineItemEndDate": "2023-01-01"}]}])

    _mock_vendor_http(monkeypatch, handler)
    async with wl._VendorSession() as session:
        result = await session.lookup("hp", "5CG1234XYZ")
    assert result.end_date == date(2023, 1, 1)


@pytest.mark.anyio
async def test_lenovo_uses_support_site_without_credentials(monkeypatch):
    monkeypatch.setattr(wl, "get_settings", lambda: _settings())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "pcsupport.lenovo.com"
        assert json.loads(request.content)["serialNumber"] == "PF1ABCDE"
        return httpx.Response(200, json={"code": 0, "data": {"baseWarranties": [
            {"startDate": "2021-01-01", "endDate": "2024-01-01"}]}})

    _mock_vendor_http(monkeypatch, handler)
    async with wl._VendorSession() as session:
        result = await session.lookup("lenovo", "PF1ABCDE")
    assert result.status == "found"


@pytest.mark.anyio
async def test_vendor_rate_limit_is_reported_as_error(monkeypatch):
    monkeypatch.setattr(wl, "get_settings", lambda: _settings())
    _mock_vendor_http(monkeypatch, lambda request: httpx.Response(429))
    async with wl._VendorSession() as session:
        result = await session.lookup("lenovo", "PF1ABCDE")
    assert result.status == "error"
    assert "rate limited" in result.message


@pytest.mark.anyio
async def test_placeholder_serial_is_not_sent(monkeypatch):
    _mock_vendor_http(monkeypatch, lambda request: pytest.fail("no request expected"))
    async with wl._VendorSession() as session:
        result = await session.lookup("lenovo", "To be filled by O.E.M.")
    assert result.status == "not_found"


@pytest.mark.anyio
async def test_refresh_due_assets_skips_when_module_disabled(monkeypatch):
    monkeypatch.setattr(wl, "is_module_enabled", AsyncMock(return_value=False))
    listing = AsyncMock()
    monkeypatch.setattr(asset_repo, "list_assets_due_for_warranty_lookup", listing)
    assert (await wl.refresh_due_assets())["status"] == "skipped"
    listing.assert_not_awaited()


@pytest.mark.anyio
async def test_refresh_due_assets_records_each_outcome(monkeypatch):
    monkeypatch.setattr(wl, "is_module_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(wl, "get_settings", lambda: _settings())
    monkeypatch.setattr(asset_repo, "list_assets_due_for_warranty_lookup", AsyncMock(return_value=[
        {"id": 1, "serial_number": "PF1ABCDE", "motherboard_manufacturer": "LENOVO"},
        {"id": 2, "serial_number": "N1ABCD123", "motherboard_manufacturer": "ASUSTeK"},
    ]))
    monkeypatch.setattr(asset_repo, "get_rmm_hardware_for_assets", AsyncMock(return_value={}))
    recorded = AsyncMock()
    monkeypatch.setattr(asset_repo, "record_warranty_lookup", recorded)
    _mock_vendor_http(monkeypatch, lambda request: httpx.Response(200, json={"code": 0, "data": {
        "baseWarranties": [{"startDate": "2021-01-01", "endDate": "2099-01-01"}]}}))

    summary = await wl.refresh_due_assets()

    assert summary == {"status": "ok", "checked": 2, "found": 1, "not_found": 0,
                       "unsupported": 1, "not_configured": 0, "error": 0}
    first = recorded.await_args_list[0]
    assert first.args == (1,)
    assert first.kwargs["source"] == "lenovo"
    assert first.kwargs["warranty_status"] == "Active"
    assert first.kwargs["end_date"] == date(2099, 1, 1)


@pytest.mark.anyio
async def test_integration_sync_keeps_vendor_warranty_dates(monkeypatch):
    """An RMM sync that reports no warranty must not wipe dates a vendor supplied."""
    calls = []

    async def fetch_one(query, params=None):
        return {"id": 5} if "syncro_asset_id" in query else None

    async def execute(query, params=None):
        calls.append((query, params))

    monkeypatch.setattr(asset_repo.db, "fetch_one", fetch_one)
    monkeypatch.setattr(asset_repo.db, "fetch_all", AsyncMock(return_value=[]))
    monkeypatch.setattr(asset_repo.db, "execute", execute)
    monkeypatch.setattr(asset_repo, "_refresh_auto_tags", AsyncMock())

    await asset_repo.upsert_asset(company_id=1, name="pc", syncro_asset_id="s-1")
    update, params = next((q, p) for q, p in calls if q.lstrip().startswith("UPDATE assets"))

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE assets (id INTEGER PRIMARY KEY, name, type, asset_type, asset_type_source, "
        "machine_type, status, os_name, cpu_name, ram_gb, hdd_size, last_sync, boot_time, "
        "motherboard_manufacturer, form_factor, last_user, approx_age, performance_score, "
        "warranty_status, warranty_end_date, warranty_source, syncro_asset_id, tactical_asset_id, "
        "serial_number, mac_address)"
    )
    conn.execute("INSERT INTO assets (id, warranty_status, warranty_end_date, warranty_source) "
                 "VALUES (5, 'Active', '2027-01-01', 'dell'), (6, 'Old', '2020-01-01', NULL)")
    conn.execute(update.replace("%s", "?"), params)
    conn.execute(update.replace("%s", "?"), params[:-1] + (6,))
    rows = conn.execute("SELECT id, warranty_status, warranty_end_date FROM assets ORDER BY id").fetchall()
    assert rows == [(5, "Active", "2027-01-01"), (6, None, None)]


def _post(path: str, body: bytes = b""):
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


def _patch_routes(monkeypatch, record):
    from starlette.responses import Response

    from app.features.assets import routes

    flashes = []
    monkeypatch.setattr(routes, "_asset_write_context",
                        AsyncMock(return_value=({"id": 8}, {"id": 4}, 4, None)))
    monkeypatch.setattr(routes.asset_repo, "get_asset_by_id", AsyncMock(return_value=record))
    monkeypatch.setattr(routes.audit_service, "record", AsyncMock())

    def flash(url, message, level):
        flashes.append((url, message, level))
        return Response(status_code=303)

    monkeypatch.setattr(routes._main(), "flash_redirect", flash)
    return routes, flashes


@pytest.mark.anyio
async def test_manual_warranty_route_saves_dates(monkeypatch):
    routes, flashes = _patch_routes(monkeypatch, {"id": 1, "company_id": 4})
    save = AsyncMock()
    monkeypatch.setattr(routes.asset_repo, "set_manual_warranty", save)

    await routes.save_manual_asset_warranty(
        _post("/assets/1/warranty", b"warranty_start_date=2020-01-01&warranty_end_date=2021-01-01"), 1)

    save.assert_awaited_once_with(1, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1),
                                  warranty_status="Expired")
    assert flashes[0][2] == "success"


@pytest.mark.anyio
async def test_manual_warranty_route_rejects_end_before_start(monkeypatch):
    from fastapi import HTTPException

    routes, _ = _patch_routes(monkeypatch, {"id": 1, "company_id": 4})
    save = AsyncMock()
    monkeypatch.setattr(routes.asset_repo, "set_manual_warranty", save)
    with pytest.raises(HTTPException) as exc:
        await routes.save_manual_asset_warranty(
            _post("/assets/1/warranty", b"warranty_start_date=2022-01-01&warranty_end_date=2021-01-01"), 1)
    assert exc.value.status_code == 422
    save.assert_not_awaited()


@pytest.mark.anyio
async def test_warranty_routes_hide_other_companies_assets(monkeypatch):
    from fastapi import HTTPException

    routes, _ = _patch_routes(monkeypatch, {"id": 1, "company_id": 99})
    lookup = AsyncMock()
    monkeypatch.setattr(wl, "lookup_asset", lookup)
    with pytest.raises(HTTPException) as exc:
        await routes.lookup_asset_warranty(_post("/assets/1/warranty/lookup"), 1)
    assert exc.value.status_code == 404
    lookup.assert_not_awaited()


@pytest.mark.anyio
async def test_lookup_route_reports_disabled_module(monkeypatch):
    routes, flashes = _patch_routes(monkeypatch, {"id": 1, "company_id": 4})
    monkeypatch.setattr(wl, "is_module_enabled", AsyncMock(return_value=False))
    await routes.lookup_asset_warranty(_post("/assets/1/warranty/lookup"), 1)
    assert flashes[0][2] == "error"
    assert "Warranty lookup module" in flashes[0][1]


def test_warranty_lookup_module_is_registered():
    from app.core import module_capabilities
    from app.services import modules

    assert any(m["slug"] == "warranty-lookup" for m in modules.DEFAULT_MODULES)
    assert "warranty-lookup" in modules._NON_TRIGGERABLE_MODULE_SLUGS
    assert module_capabilities.modules_for_command("lookup_asset_warranties") == {"warranty-lookup"}
    assert not module_capabilities.validate_capability_registry(modules.DEFAULT_MODULES)
