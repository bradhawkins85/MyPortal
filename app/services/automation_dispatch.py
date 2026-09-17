from __future__ import annotations

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
        raise RuntimeError("Automation event handler has not been registered.")
    return await handler(event_name, context)
