"""Tests for Xero sync diagnostic information when tickets are not billed."""
import pytest
from decimal import Decimal

from app.services import xero as xero_service
from app.repositories import assets as assets_repo


@pytest.mark.asyncio
async def test_sync_company_reports_missing_hourly_rate(monkeypatch):
    """Test that sync_company reports when hourly rate is missing but billable statuses configured."""
    
    # Mock the modules service
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {
            "enabled": True,
            "slug": "xero",
            "settings": {
                "client_id": "test_client",
                "client_secret": "test_secret",
                "refresh_token": "test_token",
                "tenant_id": "test_tenant",
                "billable_statuses": '["to bill", "billable"]',
                "default_hourly_rate": "",  # Missing hourly rate
                "account_code": "400",
            },
        }
    
    async def fake_acquire_token():
        return "test_access_token"
    
    async def fake_get_company(company_id: int):
        return {
            "id": company_id,
            "name": "Test Company",
            "xero_id": "xero-123",
        }
    
    async def fake_list_recurring_items(company_id: int):
        return []
    
    async def fake_count_active_assets(*args, **kwargs):
        return 0
    
    async def fake_count_active_assets_by_type(*args, **kwargs):
        return 0
    
    from app.services import modules as modules_service
    from app.repositories import companies as company_repo
    from app.repositories import company_recurring_invoice_items as recurring_items_repo
    
    monkeypatch.setattr(modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service, "acquire_xero_access_token", fake_acquire_token)
    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(recurring_items_repo, "list_company_recurring_invoice_items", fake_list_recurring_items)
    monkeypatch.setattr(assets_repo, "count_active_assets", fake_count_active_assets)
    monkeypatch.setattr(assets_repo, "count_active_assets_by_type", fake_count_active_assets_by_type)
    
    # Call sync_company
    result = await xero_service.sync_company(company_id=1, auto_send=False)
    
    # Verify result includes diagnostic information
    assert result["status"] == "skipped"
    assert result["company_id"] == 1
    assert "tickets_skipped_reason" in result
    assert result["tickets_skipped_reason"] == "Hourly rate not configured"
    assert result["billable_tickets_found"] == 0
    assert result["ticket_line_items_created"] == 0


@pytest.mark.asyncio
async def test_sync_company_reports_invalid_billable_statuses(monkeypatch):
    """Test that sync_company reports when billable statuses are invalid."""
    
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {
            "enabled": True,
            "slug": "xero",
            "settings": {
                "client_id": "test_client",
                "client_secret": "test_secret",
                "refresh_token": "test_token",
                "tenant_id": "test_tenant",
                "billable_statuses": "",  # Empty billable statuses
                "default_hourly_rate": "100",
                "account_code": "400",
            },
        }
    
    async def fake_acquire_token():
        return "test_access_token"
    
    async def fake_get_company(company_id: int):
        return {
            "id": company_id,
            "name": "Test Company",
            "xero_id": "xero-123",
        }
    
    async def fake_list_recurring_items(company_id: int):
        return []
    
    async def fake_count_active_assets(*args, **kwargs):
        return 0
    
    async def fake_count_active_assets_by_type(*args, **kwargs):
        return 0
    
    from app.services import modules as modules_service
    from app.repositories import companies as company_repo
    from app.repositories import company_recurring_invoice_items as recurring_items_repo
    
    monkeypatch.setattr(modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service, "acquire_xero_access_token", fake_acquire_token)
    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(recurring_items_repo, "list_company_recurring_invoice_items", fake_list_recurring_items)
    monkeypatch.setattr(assets_repo, "count_active_assets", fake_count_active_assets)
    monkeypatch.setattr(assets_repo, "count_active_assets_by_type", fake_count_active_assets_by_type)
    
    # Call sync_company
    result = await xero_service.sync_company(company_id=1, auto_send=False)
    
    # Verify result
    assert result["status"] == "skipped"
    assert result["company_id"] == 1
    # Should not have tickets_skipped_reason since billable_statuses is empty (not configured)
    assert "tickets_skipped_reason" not in result or result.get("tickets_skipped_reason") is None


@pytest.mark.asyncio
async def test_sync_company_reports_no_billable_tickets(monkeypatch):
    """Test that sync_company reports when no billable tickets are found."""
    
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {
            "enabled": True,
            "slug": "xero",
            "settings": {
                "client_id": "test_client",
                "client_secret": "test_secret",
                "refresh_token": "test_token",
                "tenant_id": "test_tenant",
                "billable_statuses": '["to bill"]',
                "default_hourly_rate": "100",
                "account_code": "400",
            },
        }
    
    async def fake_acquire_token():
        return "test_access_token"
    
    async def fake_get_company(company_id: int):
        return {
            "id": company_id,
            "name": "Test Company",
            "xero_id": "xero-123",
        }
    
    async def fake_list_recurring_items(company_id: int):
        return []
    
    async def fake_list_tickets(company_id: int, limit: int = 1000):
        # Return some tickets but none with "to bill" status
        return [
            {"id": 1, "status": "open", "company_id": company_id},
            {"id": 2, "status": "closed", "company_id": company_id},
        ]
    
    async def fake_get_unbilled_reply_ids(ticket_id: int):
        return set()  # No unbilled replies
    
    async def fake_count_active_assets(*args, **kwargs):
        return 0
    
    async def fake_count_active_assets_by_type(*args, **kwargs):
        return 0
    
    from app.services import modules as modules_service
    from app.repositories import companies as company_repo
    from app.repositories import company_recurring_invoice_items as recurring_items_repo
    from app.repositories import tickets as tickets_repo
    from app.repositories import ticket_billed_time_entries as billed_time_repo
    
    monkeypatch.setattr(modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service, "acquire_xero_access_token", fake_acquire_token)
    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(recurring_items_repo, "list_company_recurring_invoice_items", fake_list_recurring_items)
    monkeypatch.setattr(tickets_repo, "list_tickets", fake_list_tickets)
    monkeypatch.setattr(billed_time_repo, "get_unbilled_reply_ids", fake_get_unbilled_reply_ids)
    monkeypatch.setattr(assets_repo, "count_active_assets", fake_count_active_assets)
    monkeypatch.setattr(assets_repo, "count_active_assets_by_type", fake_count_active_assets_by_type)
    
    # Call sync_company
    result = await xero_service.sync_company(company_id=1, auto_send=False)
    
    # Verify result includes diagnostic information
    assert result["status"] == "skipped"
    assert result["company_id"] == 1
    assert "tickets_skipped_reason" in result
    assert result["tickets_skipped_reason"] == "No tickets with unbilled time in billable status"
    assert result["billable_tickets_found"] == 0
    assert result["ticket_line_items_created"] == 0
