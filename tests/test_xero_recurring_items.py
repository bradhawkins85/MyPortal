import asyncio
import pytest
from decimal import Decimal

from app.services import xero


def test_evaluate_qty_expression_with_static_number():
    result = xero._evaluate_qty_expression("5", {})
    assert result == 5.0


def test_evaluate_qty_expression_with_variable():
    context = {"active_agents": 10}
    result = xero._evaluate_qty_expression("{active_agents}", context)
    assert result == 10.0


def test_evaluate_qty_expression_with_invalid_input():
    result = xero._evaluate_qty_expression("invalid", {})
    assert result == 1.0


def test_evaluate_qty_expression_with_missing_variable():
    result = xero._evaluate_qty_expression("{missing_var}", {})
    assert result == 1.0


def test_build_recurring_invoice_items(monkeypatch):
    async def fake_list_items(company_id):
        return [
            {
                "id": 1,
                "company_id": company_id,
                "product_code": "LABOR-MANAGED",
                "description_template": "Managed services for {company_name}",
                "qty_expression": "1",
                "price_override": 100.00,
                "active": True,
            },
            {
                "id": 2,
                "company_id": company_id,
                "product_code": "LABOR-ADHOC",
                "description_template": "Ad-hoc support",
                "qty_expression": "{active_agents}",
                "price_override": None,
                "active": True,
            },
            {
                "id": 3,
                "company_id": company_id,
                "product_code": "INACTIVE-ITEM",
                "description_template": "Should not appear",
                "qty_expression": "1",
                "price_override": None,
                "active": False,
            },
        ]

    monkeypatch.setattr(xero.recurring_items_repo, "list_company_recurring_invoice_items", fake_list_items)

    context = {"company_name": "Test Company", "active_agents": 5}
    result = asyncio.run(
        xero.build_recurring_invoice_items(
            company_id=1,
            tax_type="OUTPUT2",
            context=context,
        )
    )

    assert len(result) == 2
    assert result[0]["Description"] == "Managed services for Test Company"
    assert result[0]["Quantity"] == 1.0
    assert result[0]["ItemCode"] == "LABOR-MANAGED"
    assert result[0]["UnitAmount"] == 100.00
    assert result[0]["TaxType"] == "OUTPUT2"

    assert result[1]["Description"] == "Ad-hoc support"
    assert result[1]["Quantity"] == 5.0
    assert result[1]["ItemCode"] == "LABOR-ADHOC"
    assert "UnitAmount" not in result[1]
    assert result[1]["TaxType"] == "OUTPUT2"


def test_build_recurring_invoice_items_with_empty_list(monkeypatch):
    async def fake_list_items(company_id):
        return []

    monkeypatch.setattr(xero.recurring_items_repo, "list_company_recurring_invoice_items", fake_list_items)

    result = asyncio.run(
        xero.build_recurring_invoice_items(
            company_id=1,
            tax_type="OUTPUT2",
            context={},
        )
    )

    assert len(result) == 0


def test_build_invoice_context(monkeypatch):
    from datetime import datetime, timezone

    async def fake_count_active_assets(*, company_id=None, since=None):
        return 25

    async def fake_count_by_type(*, company_id=None, since=None, device_type=None):
        counts = {
            "Workstation": 15,
            "Server": 5,
            "User": 5,
        }
        return counts.get(device_type, 0)

    async def fake_get_company(company_id):
        return {"id": company_id, "name": "Test Company"}

    async def fake_list_field_definitions():
        return []

    monkeypatch.setattr(xero.assets_repo, "count_active_assets", fake_count_active_assets)
    monkeypatch.setattr(xero.assets_repo, "count_active_assets_by_type", fake_count_by_type)
    monkeypatch.setattr(xero.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(xero.asset_custom_fields_repo, "list_field_definitions", fake_list_field_definitions)

    result = asyncio.run(xero.build_invoice_context(company_id=1))

    assert result["company_id"] == 1
    assert result["company_name"] == "Test Company"
    assert result["active_agents"] == 25
    assert result["active_workstations"] == 15
    assert result["active_servers"] == 5
    assert result["active_users"] == 5
    assert result["total_assets"] == 25


def test_build_invoice_context_includes_custom_field_counts(monkeypatch):
    """Test that build_invoice_context includes custom field checkbox counts."""

    async def fake_count_active_assets(*, company_id=None, since=None):
        return 10

    async def fake_count_by_type(*, company_id=None, since=None, device_type=None):
        return 0

    async def fake_get_company(company_id):
        return {"id": company_id, "name": "Test Company"}

    async def fake_list_field_definitions():
        return [
            {"id": 1, "name": "bitdefender", "field_type": "checkbox"},
            {"id": 2, "name": "threatlocker-installed", "field_type": "checkbox"},
            {"id": 3, "name": "os_version", "field_type": "text"},  # Non-checkbox, should be skipped
        ]

    async def fake_count_assets_by_custom_field(company_id, field_name, field_value=True, since=None):
        # Return different counts based on whether this is a total or active query
        counts_total = {"bitdefender": 20, "threatlocker-installed": 12}
        counts_active = {"bitdefender": 15, "threatlocker-installed": 8}
        if since is not None:
            return counts_active.get(field_name, 0)
        return counts_total.get(field_name, 0)

    monkeypatch.setattr(xero.assets_repo, "count_active_assets", fake_count_active_assets)
    monkeypatch.setattr(xero.assets_repo, "count_active_assets_by_type", fake_count_by_type)
    monkeypatch.setattr(xero.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(xero.asset_custom_fields_repo, "list_field_definitions", fake_list_field_definitions)
    monkeypatch.setattr(
        xero.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )

    result = asyncio.run(xero.build_invoice_context(company_id=1))

    # Standard fields still present
    assert result["active_agents"] == 10
    assert result["company_name"] == "Test Company"

    # Checkbox custom field total counts
    assert result["cf_total_bitdefender"] == 20
    assert result["cf_total_threatlocker_installed"] == 12  # hyphens replaced with underscores

    # Checkbox custom field active counts (synced within 30 days)
    assert result["cf_active_bitdefender"] == 15
    assert result["cf_active_threatlocker_installed"] == 8

    # Non-checkbox fields should NOT be included
    assert "cf_total_os_version" not in result
    assert "cf_active_os_version" not in result


def test_build_recurring_invoice_items_with_custom_field_variable(monkeypatch):
    """Test that recurring invoice items can use custom field count variables."""

    async def fake_list_items(company_id):
        return [
            {
                "id": 1,
                "company_id": company_id,
                "product_code": "BITDEFENDER",
                "description_template": "Bitdefender for {company_name} ({cf_active_bitdefender} active seats)",
                "qty_expression": "{cf_active_bitdefender}",
                "price_override": 5.00,
                "active": True,
            },
            {
                "id": 2,
                "company_id": company_id,
                "product_code": "THREATLOCKER",
                "description_template": "ThreatLocker total installs",
                "qty_expression": "{cf_total_threatlocker_installed}",
                "price_override": 3.00,
                "active": True,
            },
        ]

    monkeypatch.setattr(xero.recurring_items_repo, "list_company_recurring_invoice_items", fake_list_items)

    context = {
        "company_name": "Acme Corp",
        "cf_active_bitdefender": 18,
        "cf_total_threatlocker_installed": 25,
    }
    result = asyncio.run(
        xero.build_recurring_invoice_items(
            company_id=1,
            tax_type="OUTPUT2",
            context=context,
        )
    )

    assert len(result) == 2

    bd_item = next(item for item in result if item["ItemCode"] == "BITDEFENDER")
    assert bd_item["Quantity"] == 18.0
    assert bd_item["Description"] == "Bitdefender for Acme Corp (18 active seats)"

    tl_item = next(item for item in result if item["ItemCode"] == "THREATLOCKER")
    assert tl_item["Quantity"] == 25.0


@pytest.mark.asyncio
async def test_build_recurring_invoice_items_fetches_xero_rates(monkeypatch):
    """Test that build_recurring_invoice_items fetches item rates from Xero."""
    
    # Track whether fetch_xero_item_rates was called
    fetch_called = {"called": False, "item_codes": []}
    
    async def fake_list_recurring_items(company_id: int):
        return [
            {
                "id": 1,
                "company_id": company_id,
                "active": True,
                "product_code": "MANAGED-SERVICES",
                "description_template": "Managed Services",
                "qty_expression": "1",
                "price_override": None,  # No override, should fetch from Xero
            },
            {
                "id": 2,
                "company_id": company_id,
                "active": True,
                "product_code": "CLOUD-BACKUP",
                "description_template": "Cloud Backup",
                "qty_expression": "1",
                "price_override": Decimal("50.00"),  # Has override, should NOT fetch
            },
            {
                "id": 3,
                "company_id": company_id,
                "active": False,  # Inactive, should be skipped
                "product_code": "INACTIVE-ITEM",
                "description_template": "Inactive Item",
                "qty_expression": "1",
                "price_override": None,
            },
        ]
    
    async def fake_fetch_xero_item_rates(item_codes, *, tenant_id, access_token):
        # Record that the function was called
        fetch_called["called"] = True
        fetch_called["item_codes"] = list(item_codes)
        
        # Return rates for items
        return {
            "MANAGED-SERVICES": Decimal("150.00"),
        }
    
    monkeypatch.setattr(xero.recurring_items_repo, "list_company_recurring_invoice_items", fake_list_recurring_items)
    monkeypatch.setattr(xero, "fetch_xero_item_rates", fake_fetch_xero_item_rates)
    
    # Call the function with credentials
    line_items = await xero.build_recurring_invoice_items(
        company_id=1,
        tax_type="OUTPUT",
        context={},
        tenant_id="test-tenant",
        access_token="test-token",
    )
    
    # Verify fetch_xero_item_rates was called
    assert fetch_called["called"], "fetch_xero_item_rates should have been called"
    assert "MANAGED-SERVICES" in fetch_called["item_codes"], "Should fetch rate for MANAGED-SERVICES"
    assert "CLOUD-BACKUP" not in fetch_called["item_codes"], "Should NOT fetch rate for CLOUD-BACKUP (has override)"
    assert "INACTIVE-ITEM" not in fetch_called["item_codes"], "Should NOT fetch rate for INACTIVE-ITEM (inactive)"
    
    # Verify line items were created correctly
    assert len(line_items) == 2, "Should have 2 line items (2 active items)"
    
    # Find the MANAGED-SERVICES item
    managed_services_item = next(item for item in line_items if item["ItemCode"] == "MANAGED-SERVICES")
    assert managed_services_item["UnitAmount"] == 150.00, "Should use Xero item rate"
    
    # Find the CLOUD-BACKUP item
    cloud_backup_item = next(item for item in line_items if item["ItemCode"] == "CLOUD-BACKUP")
    assert cloud_backup_item["UnitAmount"] == 50.00, "Should use price override"


@pytest.mark.asyncio
async def test_build_recurring_invoice_items_without_credentials(monkeypatch):
    """Test that build_recurring_invoice_items works without credentials (doesn't fetch rates)."""
    
    fetch_called = {"called": False}
    
    async def fake_list_recurring_items(company_id: int):
        return [
            {
                "id": 1,
                "company_id": company_id,
                "active": True,
                "product_code": "MANAGED-SERVICES",
                "description_template": "Managed Services",
                "qty_expression": "1",
                "price_override": None,
            },
        ]
    
    async def fake_fetch_xero_item_rates(item_codes, *, tenant_id, access_token):
        fetch_called["called"] = True
        return {}
    
    monkeypatch.setattr(xero.recurring_items_repo, "list_company_recurring_invoice_items", fake_list_recurring_items)
    monkeypatch.setattr(xero, "fetch_xero_item_rates", fake_fetch_xero_item_rates)
    
    # Call without credentials
    line_items = await xero.build_recurring_invoice_items(
        company_id=1,
        tax_type="OUTPUT",
        context={},
        # No tenant_id or access_token
    )
    
    # Verify fetch_xero_item_rates was NOT called
    assert not fetch_called["called"], "fetch_xero_item_rates should NOT have been called without credentials"
    
    # Verify line item was created (without UnitAmount, Xero will use default)
    assert len(line_items) == 1
    assert line_items[0]["ItemCode"] == "MANAGED-SERVICES"
    assert "UnitAmount" not in line_items[0], "Should not have UnitAmount without credentials"
