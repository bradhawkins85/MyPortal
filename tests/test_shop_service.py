import asyncio

from app.services import shop as shop_service


async def _fake_settings_with_webhook():
    return {"discord_webhook_url": "https://discord.example.com/webhook"}


def test_send_discord_stock_notification_enqueues_event(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_enqueue_event(**kwargs):
        captured["event_args"] = kwargs
        return {"id": 99, "status": "pending", "attempt_count": 0}

    monkeypatch.setattr(
        shop_service.shop_settings_repo,
        "get_settings",
        _fake_settings_with_webhook,
    )
    monkeypatch.setattr(shop_service.webhook_monitor, "enqueue_event", fake_enqueue_event)

    product = {"id": 10, "name": "Widget+", "sku": "SKU-1"}

    event = asyncio.run(
        shop_service.send_discord_stock_notification(product, previous_stock=5, new_stock=0)
    )

    assert event == {"id": 99, "status": "pending", "attempt_count": 0}
    event_args = captured["event_args"]
    assert event_args["name"] == "shop.discord.stock_notification"
    assert event_args["target_url"] == "https://discord.example.com/webhook"
    assert (
        event_args["payload"]["content"]
        == "⚠️ **Widget+** (SKU-1) is now **Out Of Stock** (was 5)."
    )


def test_maybe_send_discord_stock_notification_returns_event(monkeypatch):
    async def fake_get_product_by_id(*_, **__):
        return {"id": 22, "name": "Laptop", "sku": "LP-5"}

    async def fake_enqueue_event(**kwargs):
        return {"id": 101, "status": "pending", "payload": kwargs.get("payload")}

    monkeypatch.setattr(
        shop_service.shop_settings_repo,
        "get_settings",
        _fake_settings_with_webhook,
    )
    monkeypatch.setattr(shop_service.webhook_monitor, "enqueue_event", fake_enqueue_event)
    monkeypatch.setattr(shop_service.shop_repo, "get_product_by_id", fake_get_product_by_id)

    event = asyncio.run(
        shop_service.maybe_send_discord_stock_notification_by_id(
            product_id=22,
            previous_stock=0,
            new_stock=3,
        )
    )

    assert event["id"] == 101
    assert event["payload"]["content"].startswith("✅ **Laptop** (LP-5)")
