from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies.auth import require_super_admin
from app.schemas.uptimekuma import (
    UptimeKumaAlertIngestResponse,
    UptimeKumaAlertPayload,
    UptimeKumaAlertResponse,
)
from app.services import uptimekuma as uptimekuma_service

router = APIRouter(prefix="/api/integration-modules/uptimekuma", tags=["Uptime Kuma"])


@router.post(
    "/alerts",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UptimeKumaAlertIngestResponse,
    name="uptimekuma_receive_alert",
)
async def receive_alert(
    request: Request,
    payload: UptimeKumaAlertPayload,
    token: str | None = Query(default=None, description="Optional token fallback when Authorization header is unavailable."),
) -> UptimeKumaAlertIngestResponse:
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    provided_secret: str | None = None
    if auth_header and auth_header.lower().startswith("bearer "):
        provided_secret = auth_header.split(" ", 1)[1].strip()
    if not provided_secret and token:
        provided_secret = token.strip() or None

    remote_addr = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent") or request.headers.get("User-Agent")

    try:
        record = await uptimekuma_service.ingest_alert(
            payload=payload,
            raw_payload=payload.model_dump(mode="json", by_alias=True),
            provided_secret=provided_secret,
            remote_addr=remote_addr,
            user_agent=user_agent,
        )
    except uptimekuma_service.AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except uptimekuma_service.ModuleDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return UptimeKumaAlertIngestResponse(status="accepted", alert_id=record["id"])


@router.get("/alerts", response_model=list[UptimeKumaAlertResponse])
async def list_alerts(
    status_filter: str | None = Query(default=None, alias="status"),
    monitor_id: int | None = Query(default=None),
    important: bool | None = Query(default=None, description="Filter alerts marked as important."),
    search: str | None = Query(default=None, description="Case-insensitive search across message, reason, and monitor name."),
    sort_by: str = Query(default="received_at"),
    sort_direction: str = Query(default="desc"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(require_super_admin),
) -> list[UptimeKumaAlertResponse]:
    records = await uptimekuma_service.list_alerts(
        status=status_filter,
        monitor_id=monitor_id,
        importance=important,
        search=search,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )
    return [UptimeKumaAlertResponse(**record) for record in records]


@router.get("/alerts/{alert_id}", response_model=UptimeKumaAlertResponse)
async def get_alert(alert_id: int, current_user: dict = Depends(require_super_admin)) -> UptimeKumaAlertResponse:
    record = await uptimekuma_service.get_alert(alert_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    return UptimeKumaAlertResponse(**record)
