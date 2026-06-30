import asyncio

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
    line = preview["items"][0]["xeroLineItem"]
    assert line["Description"] == "Ticket #42 - Broken printer - Remote Support - 1 Hour 30 Mins"
    assert line["Quantity"] == 1.5
    assert line["UnitAmount"] == 125.5
    assert line["ItemCode"] == "LAB"
