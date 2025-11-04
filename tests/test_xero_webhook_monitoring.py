"""Tests for Xero webhook monitoring functionality."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import xero


@pytest.mark.asyncio
async def test_send_invoice_to_xero_creates_webhook_event():
    """Test that sending an invoice creates a webhook event for monitoring."""
    invoice_data = {
        "type": "ACCREC",
        "contact": {"ContactID": "test-contact-id"},
        "line_items": [
            {
                "Description": "Test Item",
                "Quantity": 1,
                "ItemCode": "TEST001",
                "UnitAmount": 100.00,
            }
        ],
        "line_amount_type": "Exclusive",
        "reference": "Test Invoice",
    }
    
    # Mock the modules service to return a valid access token
    with patch("app.services.xero.modules_service.acquire_xero_access_token") as mock_acquire:
        mock_acquire.return_value = "test-access-token"
        
        # Mock webhook_monitor.create_manual_event
        with patch("app.services.xero.webhook_monitor.create_manual_event") as mock_create_event:
            mock_create_event.return_value = {"id": 123, "status": "in_progress"}
            
            # Mock webhook_monitor.record_manual_success
            with patch("app.services.xero.webhook_monitor.record_manual_success") as mock_record_success:
                mock_record_success.return_value = {
                    "id": 123,
                    "status": "succeeded",
                    "response_status": 200,
                }
                
                # Mock the HTTP client
                with patch("app.services.xero.httpx.AsyncClient") as mock_client_class:
                    mock_client = MagicMock()
                    mock_client_class.return_value.__aenter__.return_value = mock_client
                    
                    mock_response = MagicMock()
                    mock_response.status_code = 200
                    mock_response.text = '{"Status": "OK"}'
                    mock_response.headers = {"content-type": "application/json"}
                    mock_response.raise_for_status = MagicMock()
                    
                    mock_client.post = AsyncMock(return_value=mock_response)
                    
                    # Call the function
                    result = await xero.send_invoice_to_xero(
                        invoice_data=invoice_data,
                        tenant_id="test-tenant-id",
                        company_id=1,
                    )
    
    # Verify webhook event was created
    mock_create_event.assert_called_once()
    call_args = mock_create_event.call_args
    assert call_args[1]["name"] == "module.xero.create_invoice"
    assert call_args[1]["target_url"] == "https://api.xero.com/api.xro/2.0/Invoices"
    assert "xero-tenant-id" in call_args[1]["headers"]
    assert call_args[1]["headers"]["xero-tenant-id"] == "test-tenant-id"
    
    # Verify HTTP request was made
    mock_client.post.assert_called_once()
    
    # Verify success was recorded with details
    mock_record_success.assert_called_once()
    success_call = mock_record_success.call_args
    assert success_call[1]["event_id"] == 123
    assert success_call[1]["attempt_number"] == 1
    assert success_call[1]["response_status"] == 200
    assert success_call[1]["request_headers"] is not None
    assert success_call[1]["request_body"] is not None
    
    # Verify result
    assert result["status"] == "succeeded"
    assert result["event_id"] == 123


@pytest.mark.asyncio
async def test_send_invoice_to_xero_records_failure():
    """Test that failed invoice sends are properly recorded in webhook monitor."""
    invoice_data = {
        "type": "ACCREC",
        "contact": {"ContactID": "test-contact-id"},
        "line_items": [],
        "line_amount_type": "Exclusive",
        "reference": "Test Invoice",
    }
    
    # Mock the modules service to return a valid access token
    with patch("app.services.xero.modules_service.acquire_xero_access_token") as mock_acquire:
        mock_acquire.return_value = "test-access-token"
        
        # Mock webhook_monitor.create_manual_event
        with patch("app.services.xero.webhook_monitor.create_manual_event") as mock_create_event:
            mock_create_event.return_value = {"id": 456, "status": "in_progress"}
            
            # Mock webhook_monitor.record_manual_failure
            with patch("app.services.xero.webhook_monitor.record_manual_failure") as mock_record_failure:
                mock_record_failure.return_value = {
                    "id": 456,
                    "status": "failed",
                    "response_status": 400,
                    "last_error": "HTTP 400",
                }
                
                # Mock the HTTP client to raise an HTTPStatusError
                with patch("app.services.xero.httpx.AsyncClient") as mock_client_class:
                    mock_client = MagicMock()
                    mock_client_class.return_value.__aenter__.return_value = mock_client
                    
                    mock_response = MagicMock()
                    mock_response.status_code = 400
                    mock_response.text = '{"Message": "Invalid invoice"}'
                    mock_response.headers = {"content-type": "application/json"}
                    
                    error = httpx.HTTPStatusError(
                        "Bad Request",
                        request=MagicMock(),
                        response=mock_response,
                    )
                    
                    mock_client.post = AsyncMock(side_effect=error)
                    
                    # Call the function
                    result = await xero.send_invoice_to_xero(
                        invoice_data=invoice_data,
                        tenant_id="test-tenant-id",
                        company_id=2,
                    )
    
    # Verify webhook event was created
    mock_create_event.assert_called_once()
    
    # Verify failure was recorded with full details
    mock_record_failure.assert_called_once()
    failure_call = mock_record_failure.call_args
    assert failure_call[1]["event_id"] == 456
    assert failure_call[1]["attempt_number"] == 1
    assert failure_call[1]["status"] == "failed"
    assert failure_call[1]["response_status"] == 400
    assert failure_call[1]["response_body"] == '{"Message": "Invalid invoice"}'
    assert failure_call[1]["request_headers"] is not None
    assert failure_call[1]["request_body"] is not None
    assert failure_call[1]["response_headers"] is not None
    
    # Verify result
    assert result["status"] == "failed"
    assert result["event_id"] == 456
    assert result["response_status"] == 400


@pytest.mark.asyncio
async def test_sync_company_calls_send_invoice_when_items_exist():
    """Test that sync_company calls send_invoice_to_xero when recurring items exist."""
    
    # Mock all the dependencies
    with patch("app.services.xero.modules_service.get_module") as mock_get_module:
        mock_get_module.return_value = {
            "enabled": True,
            "settings": {
                "client_id": "test-client",
                "client_secret": "test-secret",
                "refresh_token": "test-refresh",
                "tenant_id": "test-tenant",
                "tax_type": "OUTPUT",
                "line_amount_type": "Exclusive",
                "reference_prefix": "Test",
            },
        }
        
        with patch("app.services.xero.company_repo.get_company_by_id") as mock_get_company:
            mock_get_company.return_value = {
                "id": 1,
                "name": "Test Company",
                "xero_id": "xero-123",
            }
            
            with patch("app.services.xero.build_invoice_context") as mock_build_context:
                mock_build_context.return_value = {
                    "company_id": 1,
                    "active_agents": 5,
                }
                
                with patch("app.services.xero.build_recurring_invoice_items") as mock_build_items:
                    mock_build_items.return_value = [
                        {
                            "Description": "Managed Services",
                            "Quantity": 5,
                            "ItemCode": "MSP001",
                        }
                    ]
                    
                    with patch("app.services.xero.send_invoice_to_xero") as mock_send_invoice:
                        mock_send_invoice.return_value = {
                            "status": "succeeded",
                            "event_id": 789,
                            "company_id": 1,
                        }
                        
                        # Call sync_company
                        result = await xero.sync_company(company_id=1)
    
    # Verify send_invoice_to_xero was called
    mock_send_invoice.assert_called_once()
    call_args = mock_send_invoice.call_args[1]
    assert call_args["tenant_id"] == "test-tenant"
    assert call_args["company_id"] == 1
    assert "invoice_data" in call_args
    assert call_args["invoice_data"]["type"] == "ACCREC"
    assert len(call_args["invoice_data"]["line_items"]) == 1
    
    # Verify result
    assert result["status"] == "succeeded"
    assert result["event_id"] == 789
    assert result["line_items_count"] == 1


@pytest.mark.asyncio
async def test_sync_company_skips_when_no_recurring_items():
    """Test that sync_company skips when there are no recurring items."""
    
    # Mock all the dependencies
    with patch("app.services.xero.modules_service.get_module") as mock_get_module:
        mock_get_module.return_value = {
            "enabled": True,
            "settings": {
                "client_id": "test-client",
                "client_secret": "test-secret",
                "refresh_token": "test-refresh",
                "tenant_id": "test-tenant",
            },
        }
        
        with patch("app.services.xero.company_repo.get_company_by_id") as mock_get_company:
            mock_get_company.return_value = {
                "id": 1,
                "name": "Test Company",
                "xero_id": "xero-123",
            }
            
            with patch("app.services.xero.build_invoice_context") as mock_build_context:
                mock_build_context.return_value = {
                    "company_id": 1,
                    "active_agents": 0,
                }
                
                with patch("app.services.xero.build_recurring_invoice_items") as mock_build_items:
                    mock_build_items.return_value = []
                    
                    with patch("app.services.xero.send_invoice_to_xero") as mock_send_invoice:
                        # Call sync_company
                        result = await xero.sync_company(company_id=1)
    
    # Verify send_invoice_to_xero was NOT called
    mock_send_invoice.assert_not_called()
    
    # Verify result
    assert result["status"] == "skipped"
    assert result["reason"] == "No active recurring invoice items"
