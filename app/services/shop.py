from __future__ import annotations

import re
from typing import Any, Mapping

import httpx

from app.core.logging import log_error
from app.repositories import shop as shop_repo
from app.repositories import shop_settings as shop_settings_repo

_ESCAPE_PATTERN = re.compile(r"([*_`~<>@\\])")


def _escape_discord_markdown(value: str) -> str:
    return _ESCAPE_PATTERN.sub(r"\\\\\1", value)


async def send_discord_stock_notification(
    product: Mapping[str, Any],
    previous_stock: int,
    new_stock: int,
) -> None:
    settings = await shop_settings_repo.get_settings()
    webhook_url = settings.get("discord_webhook_url") if settings else None
    if not webhook_url:
        return

    content: str | None = None
    safe_name = _escape_discord_markdown(str(product.get("name") or "Product"))
    safe_sku = _escape_discord_markdown(str(product.get("sku") or "SKU"))
    adjusted_new_stock = new_stock if new_stock > 0 else 0
    if previous_stock > 0 and new_stock <= 0:
        content = f"⚠️ **{safe_name}** ({safe_sku}) is now **Out Of Stock** (was {previous_stock})."
    elif previous_stock <= 0 and new_stock > 0:
        content = (
            f"✅ **{safe_name}** ({safe_sku}) is back **In Stock** with {adjusted_new_stock} available."
        )

    if not content:
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(str(webhook_url), json={"content": content})
    except Exception as exc:  # pragma: no cover - network safety
        log_error(
            "Failed to send Discord stock notification",
            error=str(exc),
            product_id=product.get("id"),
        )


async def maybe_send_discord_stock_notification_by_id(
    product_id: int,
    previous_stock: int | None,
    new_stock: int | None,
) -> None:
    if previous_stock is None or new_stock is None:
        return
    if (previous_stock > 0 and new_stock > 0) or (previous_stock <= 0 and new_stock <= 0):
        return
    product = await shop_repo.get_product_by_id(
        product_id,
        include_archived=True,
    )
    if not product:
        return
    await send_discord_stock_notification(product, previous_stock, new_stock)
