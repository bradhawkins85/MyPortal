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
