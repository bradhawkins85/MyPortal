from __future__ import annotations

from importlib import import_module
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

TriggerModuleHandler = Callable[..., Awaitable[dict[str, Any]]]

_trigger_module_handler: TriggerModuleHandler | None = None


def register_trigger_module_handler(handler: TriggerModuleHandler) -> None:
    global _trigger_module_handler
    _trigger_module_handler = handler


async def trigger_module(
    module_slug: str,
    payload: Mapping[str, Any] | None,
    *,
    background: bool = True,
    on_complete: Callable[[Mapping[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    handler = _trigger_module_handler
    if handler is None:
        modules_service = import_module("app.services.modules")
        handler = modules_service.trigger_module
        register_trigger_module_handler(handler)
    return await handler(
        module_slug,
        payload or {},
        background=background,
        on_complete=on_complete,
    )
