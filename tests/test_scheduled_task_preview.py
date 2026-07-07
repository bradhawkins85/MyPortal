import asyncio
from unittest.mock import AsyncMock

from app.services import scheduled_task_preview


def test_preview_requires_company_for_generate_invoice():
    preview = asyncio.run(scheduled_task_preview.preview_task({"command": "generate_invoice", "company_id": None}))

    assert preview["status"] == "skipped"
    assert "Company context" in preview["summary"]


def test_preview_unsupported_command():
    preview = asyncio.run(scheduled_task_preview.preview_task({"command": "sync_staff"}))

    assert preview["status"] == "unsupported"
    assert preview["items"] == []


def test_preview_price_change_notifications(monkeypatch):
    async def fake_products():
        return [
            {
                "id": 10,
                "name": "Managed Seat",
                "sku": "MSP-SEAT",
                "category_name": "Managed Services",
                "price_change_date": "2026-07-01",
            }
        ]

    monkeypatch.setattr(
        scheduled_task_preview.subscription_price_changes,
        "get_products_with_pending_price_changes",
        fake_products,
    )

    preview = asyncio.run(scheduled_task_preview.preview_task({"command": "send_price_change_notifications"}))

    assert preview["status"] == "ready"
    assert preview["totals"]["productCount"] == 1
    assert preview["items"][0]["label"] == "Managed Seat"


def test_generate_invoice_preview_uses_xero_line_item_template(monkeypatch):
    async def fake_company(company_id):
        return {"id": company_id, "name": "Acme"}

    async def fake_settings(slug):
        return {
            "line_item_description_template": "Ticket #{ticket_id} - {ticket_subject} - {labour_name} - {labour_duration}"
        }

    async def fake_context(company_id):
        return {}

    async def fake_recurring(company_id, *, tax_type, context):
        return []

    async def fake_tickets(company_id, limit):
        return [{"id": 42, "subject": "Broken printer", "status": "Resolved"}]

    async def fake_unbilled(ticket_id):
        return [100]

    async def fake_replies(ticket_id, include_internal):
        return [
            {
                "id": 100,
                "is_billable": True,
                "minutes_spent": 90,
                "labour_type_code": "LAB",
                "labour_type_name": "Remote Support",
                "labour_type_rate": "125.50",
            }
        ]

    monkeypatch.setattr(scheduled_task_preview.company_repo, "get_company_by_id", fake_company)
    monkeypatch.setattr(scheduled_task_preview.modules_service, "get_module_settings", fake_settings)
    monkeypatch.setattr(scheduled_task_preview.xero_service, "build_invoice_context", fake_context)
    monkeypatch.setattr(scheduled_task_preview.xero_service, "build_recurring_invoice_items", fake_recurring)
    monkeypatch.setattr(scheduled_task_preview.tickets_repo, "list_tickets", fake_tickets)
    monkeypatch.setattr(scheduled_task_preview.billed_time_repo, "get_unbilled_reply_ids", fake_unbilled)
    monkeypatch.setattr(scheduled_task_preview.tickets_repo, "list_replies", fake_replies)

    preview = asyncio.run(scheduled_task_preview.preview_task({"command": "generate_invoice", "company_id": 1}))

    assert preview["status"] == "ready"
    assert preview["totals"]["ticketLineCount"] == 1
    item = preview["items"][0]
    assert item["xeroDescription"] == "Ticket #42 - Broken printer - Remote Support - 1 Hour 30 Mins"
    assert item["xeroQuantity"] == "1.50"
    assert item["xeroUnitAmount"] == "125.50"
    assert item["xeroItemCode"] == "LAB"
    assert "xeroLineItemTemplate" not in item
    assert "xeroLineItem" not in item


def test_generate_invoice_preview_prefers_env_xero_line_item_template(monkeypatch):
    async def fake_company(company_id):
        return {"id": company_id, "name": "Acme"}

    async def fake_settings(slug):
        return {
            "line_item_description_template": "Ticket {ticket_id}: {ticket_subject}{labour_suffix} ({labour_duration})"
        }

    async def fake_context(company_id):
        return {}

    async def fake_recurring(company_id, *, tax_type, context):
        return []

    async def fake_tickets(company_id, limit):
        return [{"id": 42, "subject": "Broken printer", "status": "Resolved", "requester_name": "Jane Requester"}]

    async def fake_unbilled(ticket_id):
        return [100]

    async def fake_replies(ticket_id, include_internal):
        return [
            {
                "id": 100,
                "is_billable": True,
                "minutes_spent": 90,
                "labour_type_code": "LAB",
                "labour_type_name": "Remote Support",
                "labour_type_rate": "125.50",
            }
        ]

    monkeypatch.setenv(
        "XERO_LINE_ITEM_TEMPLATE",
        "Ticket {ticket_id}: {ticket_subject} {labour_suffix} {requester_name} ({labour_duration})",
    )
    monkeypatch.setattr(scheduled_task_preview.company_repo, "get_company_by_id", fake_company)
    monkeypatch.setattr(scheduled_task_preview.modules_service, "get_module_settings", fake_settings)
    monkeypatch.setattr(scheduled_task_preview.xero_service, "build_invoice_context", fake_context)
    monkeypatch.setattr(scheduled_task_preview.xero_service, "build_recurring_invoice_items", fake_recurring)
    monkeypatch.setattr(scheduled_task_preview.tickets_repo, "list_tickets", fake_tickets)
    monkeypatch.setattr(scheduled_task_preview.billed_time_repo, "get_unbilled_reply_ids", fake_unbilled)
    monkeypatch.setattr(scheduled_task_preview.tickets_repo, "list_replies", fake_replies)

    preview = asyncio.run(scheduled_task_preview.preview_task({"command": "generate_invoice", "company_id": 1}))

    assert preview["status"] == "ready"
    assert preview["items"][0]["xeroDescription"] == (
        "Ticket 42: Broken printer Remote Support Jane Requester (1 Hour 30 Mins)"
    )


def test_generate_invoice_preview_resolves_requester_name_for_template(monkeypatch):
    async def fake_company(company_id):
        return {"id": company_id, "name": "Acme"}

    async def fake_context(company_id):
        return {}

    async def fake_recurring(company_id, *, tax_type, context):
        return []

    async def fake_tickets(company_id, limit):
        return [{"id": 42, "subject": "Broken printer", "status": "Resolved", "requester_id": 99}]

    async def fake_unbilled(ticket_id):
        return [100]

    async def fake_replies(ticket_id, include_internal):
        return [
            {
                "id": 100,
                "is_billable": True,
                "minutes_spent": 90,
                "labour_type_code": "LAB",
                "labour_type_name": "Remote Support",
                "labour_type_rate": "125.50",
            }
        ]

    monkeypatch.setenv("XERO_LINE_ITEM_TEMPLATE", "Ticket {ticket_id}: {ticket_subject} - {requester_name}")
    monkeypatch.setattr(scheduled_task_preview.company_repo, "get_company_by_id", fake_company)
    monkeypatch.setattr(scheduled_task_preview.xero_service, "build_invoice_context", fake_context)
    monkeypatch.setattr(scheduled_task_preview.xero_service, "build_recurring_invoice_items", fake_recurring)
    monkeypatch.setattr(scheduled_task_preview.tickets_repo, "list_tickets", fake_tickets)
    monkeypatch.setattr(scheduled_task_preview.billed_time_repo, "get_unbilled_reply_ids", fake_unbilled)
    monkeypatch.setattr(scheduled_task_preview.tickets_repo, "list_replies", fake_replies)
    monkeypatch.setattr(
        scheduled_task_preview.invoice_generator_service.xero_service.users_repo,
        "get_user_by_id",
        AsyncMock(return_value={"id": 99, "first_name": "Jane", "last_name": "Requester", "email": "jane@example.com"}),
    )

    preview = asyncio.run(scheduled_task_preview.preview_task({"command": "generate_invoice", "company_id": 1}))

    assert preview["status"] == "ready"
    assert preview["items"][0]["xeroDescription"] == "Ticket 42: Broken printer - Jane Requester"


def test_unbill_time_entries_preview_lists_billable_unbilled_entries(monkeypatch):
    async def fake_tickets(company_id, limit):
        return [{"id": 42, "ticket_number": "T-42", "subject": "AYCE support"}]

    async def fake_unbilled(ticket_id):
        return {100}

    async def fake_replies(ticket_id, include_internal):
        return [
            {"id": 100, "is_billable": True, "minutes_spent": 45, "labour_type_name": "Remote Support"},
            {"id": 101, "is_billable": True, "minutes_spent": 30, "labour_type_name": "Remote Support"},
        ]

    monkeypatch.setattr(scheduled_task_preview.unbill_time_entries_service.tickets_repo, "list_tickets", fake_tickets)
    monkeypatch.setattr(scheduled_task_preview.unbill_time_entries_service.billed_time_repo, "get_unbilled_reply_ids", fake_unbilled)
    monkeypatch.setattr(scheduled_task_preview.unbill_time_entries_service.tickets_repo, "list_replies", fake_replies)

    preview = asyncio.run(scheduled_task_preview.preview_task({"command": "unbill_time_entries", "company_id": 1}))

    assert preview["status"] == "ready"
    assert preview["totals"] == {"timeEntryCount": 1, "minutes": 45}
    assert preview["items"][0]["label"] == "Ticket #T-42: AYCE support"
    assert preview["items"][0]["action"].startswith("Mark this billable time entry")
