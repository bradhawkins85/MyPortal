import base64
import hashlib
import hmac
from unittest.mock import AsyncMock

import pytest

from app.api.routes import xero


def test_verify_xero_webhook_signature_accepts_valid_hmac():
    body = b'{"events":[]}'
    key = "xero-webhook-key"
    signature = base64.b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest()).decode()

    assert xero._verify_xero_webhook_signature(body, signature, key) is True
    assert xero._verify_xero_webhook_signature(body, signature, "wrong") is False


def test_extract_xero_invoice_id_from_resource_url():
    event = {"resourceUrl": "https://api.xero.com/api.xro/2.0/Invoices/inv-123"}

    assert xero._extract_xero_invoice_id(event) == "inv-123"


@pytest.mark.anyio("asyncio")
async def test_apply_xero_invoice_event_marks_local_invoice_paid(monkeypatch):
    local_invoice = {
        "id": 42,
        "company_id": 7,
        "invoice_number": "INV-001",
        "status": "xero",
        "xero_invoice_id": "xero-invoice-id",
    }
    updated_invoice = local_invoice | {"status": "paid"}

    monkeypatch.setattr(
        xero,
        "_fetch_xero_invoice",
        AsyncMock(return_value={"InvoiceID": "xero-invoice-id", "InvoiceNumber": "INV-001", "Status": "PAID"}),
    )
    monkeypatch.setattr(xero.invoice_repo, "get_invoice_by_xero_invoice_id", AsyncMock(return_value=local_invoice))
    monkeypatch.setattr(xero.invoice_repo, "get_invoice_by_number", AsyncMock(return_value=None))
    patch_mock = AsyncMock(return_value=updated_invoice)
    monkeypatch.setattr(xero.invoice_repo, "patch_invoice", patch_mock)
    audit_mock = AsyncMock()
    monkeypatch.setattr(xero.audit_service, "record", audit_mock)

    result = await xero._apply_xero_invoice_event(
        {"eventCategory": "INVOICE", "resourceId": "xero-invoice-id"},
        request=None,
    )

    assert result == {"status": "updated", "invoice_id": 42}
    patch_mock.assert_awaited_once_with(42, status="paid")
    audit_mock.assert_awaited_once()
