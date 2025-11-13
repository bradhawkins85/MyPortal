"""Tests for Xero sync diagnostic information when tickets are not billed."""
import pytest
from decimal import Decimal

from app.services import xero as xero_service
from app.repositories import assets as assets_repo


@pytest.mark.asyncio
async def test_sync_company_reports_missing_hourly_rate(monkeypatch):
    """Test that sync_company fetches Xero item rates when default hourly rate is missing."""
    
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
    
    async def fake_list_tickets(company_id: int, **kwargs):
        # Return no tickets so we skip due to no billable tickets
        return []
    
    async def fake_count_active_assets(*args, **kwargs):
        return 0
    
    async def fake_count_active_assets_by_type(*args, **kwargs):
        return 0
    
    from app.services import modules as modules_service
    from app.repositories import companies as company_repo
    from app.repositories import company_recurring_invoice_items as recurring_items_repo
    from app.repositories import tickets as tickets_repo
    
    monkeypatch.setattr(modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service, "acquire_xero_access_token", fake_acquire_token)
    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(recurring_items_repo, "list_company_recurring_invoice_items", fake_list_recurring_items)
    monkeypatch.setattr(tickets_repo, "list_tickets", fake_list_tickets)
    monkeypatch.setattr(assets_repo, "count_active_assets", fake_count_active_assets)
    monkeypatch.setattr(assets_repo, "count_active_assets_by_type", fake_count_active_assets_by_type)
    
    # Call sync_company
    result = await xero_service.sync_company(company_id=1, auto_send=False)
    
    # Verify result - should be skipped due to no billable tickets, not hourly rate
    assert result["status"] == "skipped"
    assert result["company_id"] == 1
    assert "tickets_skipped_reason" in result
    # The skip reason should be about no tickets, not about hourly rate
    assert result["tickets_skipped_reason"] == "No tickets with unbilled time in billable status"
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


@pytest.mark.asyncio
async def test_sync_company_skips_when_no_rates_available(monkeypatch):
    """Test that sync_company skips tickets when neither default rate nor Xero item rates are available."""
    
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
                "default_hourly_rate": "",  # No default hourly rate
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
    
    async def fake_list_tickets(company_id: int, **kwargs):
        # Return a ticket with "to bill" status
        return [
            {"id": 1, "status": "to bill", "company_id": company_id, "subject": "Test ticket"},
        ]
    
    async def fake_get_unbilled_reply_ids(ticket_id: int):
        # Return some unbilled reply IDs
        return {100, 101}
    
    async def fake_list_replies(ticket_id: int, **kwargs):
        # Return replies with labour codes
        return [
            {"id": 100, "minutes_spent": 60, "is_billable": True, "labour_type_code": "REMOTE", "labour_type_name": "Remote Support"},
        ]
    
    async def fake_fetch_xero_item_rates(item_codes, *, tenant_id, access_token):
        # Return empty dict - no Xero item rates found
        return {}
    
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
    monkeypatch.setattr(tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(billed_time_repo, "get_unbilled_reply_ids", fake_get_unbilled_reply_ids)
    monkeypatch.setattr(xero_service, "fetch_xero_item_rates", fake_fetch_xero_item_rates)
    monkeypatch.setattr(assets_repo, "count_active_assets", fake_count_active_assets)
    monkeypatch.setattr(assets_repo, "count_active_assets_by_type", fake_count_active_assets_by_type)
    
    # Call sync_company
    result = await xero_service.sync_company(company_id=1, auto_send=False)
    
    # Verify result - should be skipped due to no rates available
    assert result["status"] == "skipped"
    assert result["company_id"] == 1
    assert "tickets_skipped_reason" in result
    # The skip reason should indicate no rates are available
    assert "No billing rates configured" in result["tickets_skipped_reason"]
    assert result["billable_tickets_found"] == 1
    assert result["ticket_line_items_created"] == 0


@pytest.mark.asyncio
async def test_build_ticket_invoices_uses_xero_item_rates():
    """Test that build_ticket_invoices uses Xero item rates when provided."""
    from decimal import Decimal
    
    async def fake_fetch_ticket(ticket_id: int):
        return {
            "id": ticket_id,
            "company_id": 1,
            "subject": f"Test Ticket #{ticket_id}",
            "status": "to bill",
        }

    async def fake_fetch_replies(ticket_id: int):
        return [
            {
                "id": 100,
                "minutes_spent": 60,
                "is_billable": True,
                "labour_type_code": "REMOTE",
                "labour_type_name": "Remote Support",
            },
            {
                "id": 101,
                "minutes_spent": 120,
                "is_billable": True,
                "labour_type_code": "ONSITE",
                "labour_type_name": "On-site Support",
            },
        ]

    async def fake_fetch_company(company_id: int):
        return {"id": company_id, "name": "Test Corp", "xero_id": "xero-456"}

    # Build invoices with Xero item rates
    xero_item_rates = {
        "REMOTE": Decimal("95.00"),
        "ONSITE": Decimal("150.00"),
    }
    
    invoices = await xero_service.build_ticket_invoices(
        [1],
        hourly_rate=Decimal("100"),  # Default rate - should not be used for items with Xero rates
        account_code="400",
        tax_type="OUTPUT",
        line_amount_type="Exclusive",
        reference_prefix="IT Support",
        xero_item_rates=xero_item_rates,
        fetch_ticket=fake_fetch_ticket,
        fetch_replies=fake_fetch_replies,
        fetch_company=fake_fetch_company,
    )

    assert len(invoices) == 1
    invoice = invoices[0]
    
    # Should have 2 line items (one per labour type)
    assert len(invoice["line_items"]) == 2
    
    # Find the line items by ItemCode
    remote_item = next(item for item in invoice["line_items"] if item.get("ItemCode") == "REMOTE")
    onsite_item = next(item for item in invoice["line_items"] if item.get("ItemCode") == "ONSITE")
    
    # Verify REMOTE uses Xero item rate: 60 minutes = 1 hour @ $95
    assert remote_item["Quantity"] == 1.0
    assert remote_item["UnitAmount"] == 95.00
    
    # Verify ONSITE uses Xero item rate: 120 minutes = 2 hours @ $150
    assert onsite_item["Quantity"] == 2.0
    assert onsite_item["UnitAmount"] == 150.00
