"""Utilities for queuing long-running background tasks."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import uuid4

from app.core.logging import log_error

T = TypeVar("T")


_BACKGROUND_TASKS: dict[str, asyncio.Task[Any]] = {}


async def _maybe_await(result: Any) -> None:
    if inspect.isawaitable(result):
        await result  # type: ignore[func-returns-value]


def queue_background_task(
    task_factory: Callable[[], Awaitable[T]],
    *,
    task_id: str | None = None,
    description: str | None = None,
    on_complete: Callable[[T], Any] | None = None,
    on_error: Callable[[Exception], Any] | None = None,
) -> str:
    """Schedule an awaitable produced by ``task_factory`` to run in the background.

    The returned ``task_id`` can be used to correlate logs. Optional callbacks can
    observe completion or failure outcomes. Callbacks may be synchronous functions
    or coroutines.
    """

    loop = asyncio.get_running_loop()
    resolved_task_id = task_id or uuid4().hex

    async def _execute() -> T:
        try:
            awaitable = task_factory()
        except Exception as exc:  # pragma: no cover - defensive guard
            await _handle_error(exc)
            raise
        try:
            result = await awaitable
        except Exception as exc:
            await _handle_error(exc)
            raise
        else:
            if on_complete is not None:
                try:
                    await _maybe_await(on_complete(result))
                except Exception as callback_exc:  # pragma: no cover - defensive logging
                    log_error(
                        "Background task completion callback failed",
                        task_id=resolved_task_id,
                        error=str(callback_exc),
                    )
            return result

    async def _handle_error(exc: Exception) -> None:
        if on_error is not None:
            try:
                await _maybe_await(on_error(exc))
            except Exception as callback_exc:  # pragma: no cover - defensive logging
                log_error(
                    "Background task error callback failed",
                    task_id=resolved_task_id,
                    error=str(callback_exc),
                )
        else:
            log_error(
                "Background task failed", task_id=resolved_task_id, error=str(exc)
            )

    task = loop.create_task(
        _execute(), name=description or f"background-task-{resolved_task_id}"
    )
    _BACKGROUND_TASKS[resolved_task_id] = task

    def _cleanup(completed: asyncio.Task[T]) -> None:
        _BACKGROUND_TASKS.pop(resolved_task_id, None)
        try:
            completed.result()
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Background task raised an exception",
                task_id=resolved_task_id,
                error=str(exc),
            )

    task.add_done_callback(_cleanup)
    return resolved_task_id

