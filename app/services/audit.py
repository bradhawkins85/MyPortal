from __future__ import annotations

from typing import Any

from fastapi import Request

from app.repositories import audit_logs as audit_repo


async def log_action(
    *,
    action: str,
    user_id: int | None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    previous_value: Any = None,
    new_value: Any = None,
    metadata: dict[str, Any] | None = None,
    request: Request | None = None,
    api_key: str | None = None,
) -> None:
    ip_address = None
    if request is not None:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            ip_address = forwarded.split(",")[0].strip()
        elif request.client:
            ip_address = request.client.host
    await audit_repo.create_audit_log(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        previous_value=previous_value,
        new_value=new_value,
        metadata=metadata,
        api_key=api_key,
        ip_address=ip_address,
    )
