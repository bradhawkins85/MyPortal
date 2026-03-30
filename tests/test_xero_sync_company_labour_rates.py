"""Tests for syncing stored MyPortal invoices to Xero."""
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import xero as xero_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_sync_company_uploads_unsynchronised_invoice_lines_to_xero():
    module_settings = {
        "enabled": True,
        "settings": {
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "refresh_token": "test-refresh-token",
            "tenant_id": "test-tenant-id",
            "account_code": "400",
            "tax_type": "OUTPUT",
            "line_amount_type": "Exclusive",
        },
    }
    company = {
        "id": 1,
        "name": "Test Company",
        "xero_id": "xero-test-123",
    }
    invoice = {
        "id": 10,
        "invoice_number": "INV-202603-0001",
        "due_date": date(2026, 4, 1),
    }
    invoice_lines = [
        {
            "description": "Managed services",
            "quantity": 1,
            "unit_amount": 150.00,
            "product_code": "MSP",
        },
        {
            "description": "Remote labour",
            "quantity": 2.5,
            "unit_amount": 95.00,
            "product_code": "REMOTE",
        },
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps(
        {
            "Invoices": [
                {
                    "InvoiceID": "xero-invoice-id",
                    "InvoiceNumber": "XERO-2001",
                    "Status": "DRAFT",
                }
            ]
        }
    )
    mock_response.headers = {}

    with patch("app.services.xero.modules_service") as mock_modules, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.invoice_repo") as mock_invoice_repo, \
         patch("app.services.xero.invoice_lines_repo") as mock_invoice_lines_repo, \
         patch("app.services.xero.billed_time_repo") as mock_billed_repo, \
         patch("app.services.xero.tickets_repo") as mock_tickets_repo, \
         patch("app.services.xero.webhook_monitor") as mock_webhook, \
         patch("app.services.xero.httpx.AsyncClient") as mock_client:

        mock_modules.get_module = AsyncMock(return_value=module_settings)
        mock_modules.acquire_xero_access_token = AsyncMock(return_value="test-access-token")
        mock_company_repo.get_company_by_id = AsyncMock(return_value=company)
        mock_invoice_repo.list_unsynced_company_invoices = AsyncMock(return_value=[invoice])
        mock_invoice_repo.patch_invoice = AsyncMock()
        mock_invoice_lines_repo.list_invoice_lines = AsyncMock(return_value=invoice_lines)
        mock_billed_repo.rename_invoice_number = AsyncMock()
        mock_tickets_repo.rename_xero_invoice_number = AsyncMock()
        mock_webhook.create_manual_event = AsyncMock(return_value={"id": 1})
        mock_webhook.record_manual_success = AsyncMock()

        mock_client_instance = MagicMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client.return_value.__aexit__ = AsyncMock()

        result = await xero_service.sync_company(company_id=1)

        assert result["status"] == "succeeded"
        assert result["synced_count"] == 1
        assert result["synced_invoices"][0]["invoice_number"] == "XERO-2001"

        call_args = mock_client_instance.post.call_args
        invoice_payload = call_args[1]["json"]["Invoices"][0]
        assert invoice_payload["Contact"] == {"ContactID": "xero-test-123"}
        assert invoice_payload["Reference"] == "MyPortal INV-202603-0001"
        assert invoice_payload["DueDate"] == "2026-04-01"
        assert invoice_payload["LineItems"] == [
            {
                "Description": "Managed services",
                "Quantity": 1.0,
                "UnitAmount": 150.0,
                "AccountCode": "400",
                "ItemCode": "MSP",
                "TaxType": "OUTPUT",
            },
            {
                "Description": "Remote labour",
                "Quantity": 2.5,
                "UnitAmount": 95.0,
                "AccountCode": "400",
                "ItemCode": "REMOTE",
                "TaxType": "OUTPUT",
            },
        ]

        mock_invoice_repo.patch_invoice.assert_awaited_once()
        mock_billed_repo.rename_invoice_number.assert_awaited_once_with(1, "INV-202603-0001", "XERO-2001")
        mock_tickets_repo.rename_xero_invoice_number.assert_awaited_once_with(1, "INV-202603-0001", "XERO-2001")


@pytest.mark.anyio("asyncio")
async def test_sync_company_auto_send_marks_invoice_authorised_and_sent():
    module_settings = {
        "enabled": True,
        "settings": {
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "refresh_token": "test-refresh-token",
            "tenant_id": "test-tenant-id",
            "account_code": "400",
            "line_amount_type": "Exclusive",
        },
    }
    company = {
        "id": 1,
        "name": "Test Company",
        "xero_id": "xero-test-123",
    }
    invoice = {
        "id": 10,
        "invoice_number": "INV-202603-0001",
        "due_date": None,
    }
    invoice_lines = [
        {
            "description": "Managed services",
            "quantity": 1,
            "unit_amount": 150.00,
            "product_code": None,
        },
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps({"Invoices": [{"InvoiceID": "xero-invoice-id", "InvoiceNumber": "XERO-2002"}]})
    mock_response.headers = {}

    with patch("app.services.xero.modules_service") as mock_modules, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.invoice_repo") as mock_invoice_repo, \
         patch("app.services.xero.invoice_lines_repo") as mock_invoice_lines_repo, \
         patch("app.services.xero.billed_time_repo") as mock_billed_repo, \
         patch("app.services.xero.tickets_repo") as mock_tickets_repo, \
         patch("app.services.xero.webhook_monitor") as mock_webhook, \
         patch("app.services.xero.httpx.AsyncClient") as mock_client:

        mock_modules.get_module = AsyncMock(return_value=module_settings)
        mock_modules.acquire_xero_access_token = AsyncMock(return_value="test-access-token")
        mock_company_repo.get_company_by_id = AsyncMock(return_value=company)
        mock_invoice_repo.list_unsynced_company_invoices = AsyncMock(return_value=[invoice])
        mock_invoice_repo.patch_invoice = AsyncMock()
        mock_invoice_lines_repo.list_invoice_lines = AsyncMock(return_value=invoice_lines)
        mock_billed_repo.rename_invoice_number = AsyncMock()
        mock_tickets_repo.rename_xero_invoice_number = AsyncMock()
        mock_webhook.create_manual_event = AsyncMock(return_value={"id": 1})
        mock_webhook.record_manual_success = AsyncMock()

        mock_client_instance = MagicMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client.return_value.__aexit__ = AsyncMock()

        result = await xero_service.sync_company(company_id=1, auto_send=True)

        assert result["status"] == "succeeded"
        call_args = mock_client_instance.post.call_args
        invoice_payload = call_args[1]["json"]["Invoices"][0]
        assert invoice_payload["Status"] == "AUTHORISED"
        assert invoice_payload["SentToContact"] is True
