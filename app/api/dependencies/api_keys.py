from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.api.dependencies.database import require_database
from app.repositories import api_keys as api_key_repo


async def require_api_key(
    request: Request,
    _: None = Depends(require_database),
) -> dict:
    api_key_value = request.headers.get("x-api-key")
    if not api_key_value:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key required")
    record = await api_key_repo.get_api_key_record(api_key_value)
    if not record:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        ip_address = forwarded.split(",")[0].strip()
    elif request.client:
        ip_address = request.client.host
    else:
        ip_address = ""
    await api_key_repo.record_api_key_usage(record["id"], ip_address or "unknown")
    request.state.api_key_id = record["id"]
    return record
