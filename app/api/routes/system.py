from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies.auth import require_super_admin
from app.services import audit as audit_service
from app.services.realtime import refresh_notifier

router = APIRouter(prefix="/api/system", tags=["System"])


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
async def trigger_refresh(
    request: Request,
    current_user: dict = Depends(require_super_admin),
) -> dict[str, int | str]:
    """Broadcast a refresh notification to all connected websocket clients."""

    result = await refresh_notifier.broadcast_refresh()
    await audit_service.log_action(
        action="system.refresh.broadcast",
        user_id=current_user.get("id"),
        metadata={
            "attempted": result.attempted,
            "delivered": result.delivered,
            "dropped": result.dropped,
        },
        request=request,
    )
    return {
        "status": "broadcast",
        "attempted": result.attempted,
        "delivered": result.delivered,
        "dropped": result.dropped,
    }
