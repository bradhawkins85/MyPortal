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


def test_empty_xero_webhook_response_omits_content_length():
    response = xero._empty_xero_webhook_response(200)

    assert response.status_code == 200
    assert response.body == b""
    assert "content-length" not in response.headers


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


@pytest.mark.anyio("asyncio")
async def test_apply_xero_invoice_event_ignores_unknown_local_invoice_without_fetch(monkeypatch):
    fetch_mock = AsyncMock()
    monkeypatch.setattr(xero, "_fetch_xero_invoice", fetch_mock)
    monkeypatch.setattr(xero.invoice_repo, "get_invoice_by_xero_invoice_id", AsyncMock(return_value=None))
    monkeypatch.setattr(xero.invoice_repo, "patch_invoice", AsyncMock())
    monkeypatch.setattr(xero.audit_service, "record", AsyncMock())

    result = await xero._apply_xero_invoice_event(
        {"eventCategory": "INVOICE", "resourceId": "foreign-xero-invoice-id"},
        request=None,
    )

    assert result == {"status": "ignored", "reason": "local invoice not found"}
    fetch_mock.assert_not_awaited()
    xero.invoice_repo.patch_invoice.assert_not_awaited()
    xero.audit_service.record.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_receive_webhook_acknowledges_valid_delivery_when_event_processing_fails(monkeypatch):
    from starlette.requests import Request
    from app.services import webhook_monitor

    body = b'{"events":[{"eventCategory":"INVOICE","resourceId":"xero-invoice-id"}]}'
    key = "xero-webhook-key"
    signature = base64.b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest()).decode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/integration-modules/xero/webhook",
            "headers": [
                (b"host", b"testserver"),
                (b"x-xero-signature", signature.encode()),
                (b"content-type", b"application/json"),
            ],
            "scheme": "https",
            "server": ("testserver", 443),
            "client": ("127.0.0.1", 12345),
        },
        receive,
    )

    monkeypatch.setattr(
        xero,
        "_ensure_module_enabled",
        AsyncMock(return_value={"settings": {"webhook_key": key}}),
    )
    monkeypatch.setattr(
        xero,
        "_apply_xero_invoice_event",
        AsyncMock(side_effect=RuntimeError("Xero API timeout")),
    )
    log_mock = AsyncMock()
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", log_mock)

    response = await xero.receive_webhook(request)

    assert response.status_code == 200
    assert response.body == b""
    log_mock.assert_awaited_once()
    assert log_mock.await_args.kwargs["response_status"] == 200
    assert log_mock.await_args.kwargs["error_message"] == "Internal processing error"


@pytest.mark.anyio("asyncio")
async def test_receive_webhook_returns_empty_401_for_invalid_intent_signature(monkeypatch):
    from starlette.requests import Request
    from app.services import webhook_monitor

    body = b'{"events":[],"firstEventSequence":0,"lastEventSequence":0,"entropy":"BAD"}'

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/integration-modules/xero/webhook",
            "headers": [
                (b"host", b"testserver"),
                (b"x-xero-signature", b"not-a-valid-signature"),
                (b"content-type", b"application/json"),
            ],
            "scheme": "https",
            "server": ("testserver", 443),
            "client": ("127.0.0.1", 12345),
        },
        receive,
    )

    monkeypatch.setattr(
        xero,
        "_ensure_module_enabled",
        AsyncMock(return_value={"settings": {"webhook_key": "xero-webhook-key"}}),
    )
    log_mock = AsyncMock()
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", log_mock)

    response = await xero.receive_webhook(request)

    assert response.status_code == 401
    assert response.body == b""
    log_mock.assert_awaited_once()
    assert log_mock.await_args.kwargs["response_status"] == 401
    assert log_mock.await_args.kwargs["response_body"] == ""


@pytest.mark.anyio("asyncio")
async def test_receive_webhook_returns_empty_200_for_valid_intent_payload(monkeypatch):
    from starlette.requests import Request
    from app.services import webhook_monitor

    body = b'{"events":[],"firstEventSequence":0,"lastEventSequence":0,"entropy":"GOOD"}'
    key = "xero-webhook-key"
    signature = base64.b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest()).decode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/integration-modules/xero/webhook",
            "headers": [
                (b"host", b"testserver"),
                (b"x-xero-signature", signature.encode()),
                (b"content-type", b"application/json"),
            ],
            "scheme": "https",
            "server": ("testserver", 443),
            "client": ("127.0.0.1", 12345),
        },
        receive,
    )

    monkeypatch.setattr(
        xero,
        "_ensure_module_enabled",
        AsyncMock(return_value={"settings": {"webhook_key": key}}),
    )
    apply_mock = AsyncMock()
    monkeypatch.setattr(xero, "_apply_xero_invoice_event", apply_mock)
    log_mock = AsyncMock()
    monkeypatch.setattr(webhook_monitor, "log_incoming_webhook", log_mock)

    response = await xero.receive_webhook(request)

    assert response.status_code == 200
    assert response.body == b""
    apply_mock.assert_not_awaited()
    log_mock.assert_awaited_once()
    assert log_mock.await_args.kwargs["response_status"] == 200
