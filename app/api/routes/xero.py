from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger

from app.services import modules as modules_service

router = APIRouter(prefix="/api/integration-modules/xero", tags=["Xero"])


async def _ensure_module_enabled() -> dict[str, Any]:
    module = await modules_service.get_module("xero", redact=False)
    if not module or not module.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Xero module is disabled",
        )
    return module


@router.post(
    "/callback",
    status_code=status.HTTP_202_ACCEPTED,
    name="xero_receive_callback",
)
async def receive_callback(request: Request) -> dict[str, str]:
    await _ensure_module_enabled()

    body = await request.body()
    payload: dict[str, Any]
    if not body:
        payload = {}
    else:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload",
            ) from exc

    xero_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower().startswith("x-xero-")
    }
    remote_addr = request.client.host if request.client else None
    logger.info(
        "Received Xero webhook callback",
        remote_addr=remote_addr,
        xero_headers=xero_headers,
        payload_keys=sorted(payload.keys()),
    )
    return {"status": "accepted"}


@router.get("/callback", name="xero_callback_probe")
async def probe_callback(request: Request) -> dict[str, str]:
    """Expose a lightweight probe endpoint for connectivity checks."""

    await _ensure_module_enabled()
    if request.query_params:
        logger.info(
            "Received Xero callback probe",
            query_params=dict(request.query_params),
        )
    return {"status": "ok"}
