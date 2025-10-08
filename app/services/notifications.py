from __future__ import annotations

from typing import Any

from app.repositories import notifications as notifications_repo


async def emit_notification(
    *,
    event_type: str,
    message: str,
    user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Create a notification record for the supplied event."""

    await notifications_repo.create_notification(
        event_type=event_type,
        message=message,
        user_id=user_id,
        metadata=metadata or {},
    )
