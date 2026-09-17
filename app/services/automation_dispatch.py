from __future__ import annotations

from importlib import import_module
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

AutomationEventHandler = Callable[
    [str, Mapping[str, Any] | None],
    Awaitable[list[dict[str, Any]]],
]

_event_handler: AutomationEventHandler | None = None


def register_event_handler(handler: AutomationEventHandler) -> None:
    global _event_handler
    _event_handler = handler


async def handle_event(
    event_name: str,
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    handler = _event_handler
    if handler is None:
        automations_service = import_module("app.services.automations")
        handler = automations_service.handle_event
        register_event_handler(handler)
    return await handler(event_name, context)
