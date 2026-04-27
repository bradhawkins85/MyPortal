from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies.auth import require_super_admin
from app.services import audit as audit_service
from app.services import demo_seeding as demo_seeding_service
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


@router.get("/demo/status", status_code=status.HTTP_200_OK)
async def get_demo_status(
    current_user: dict = Depends(require_super_admin),
) -> dict[str, bool]:
    """Return whether demo data is currently seeded."""
    seeded = await demo_seeding_service.is_demo_seeded()
    return {"seeded": seeded}


@router.post("/demo/seed", status_code=status.HTTP_200_OK)
async def seed_demo(
    request: Request,
    current_user: dict = Depends(require_super_admin),
) -> dict:
    """
    Seed the Demo Company with sample data.

    Idempotent – returns immediately if demo data already exists.
    Re-run ``DELETE /api/system/demo`` first to reset and re-seed.
    """
    result = await demo_seeding_service.seed_demo_data(
        seeded_by_user_id=current_user.get("id")
    )
    await audit_service.log_action(
        action="system.demo.seed",
        user_id=current_user.get("id"),
        metadata=result,
        request=request,
    )
    return result


@router.delete("/demo", status_code=status.HTTP_200_OK)
async def remove_demo(
    request: Request,
    current_user: dict = Depends(require_super_admin),
) -> dict:
    """
    Remove all Demo Company data.

    Deletes the demo company and all related records.  Safe to call even when
    no demo data exists (returns a ``skipped`` flag in that case).
    """
    result = await demo_seeding_service.remove_demo_data()
    await audit_service.log_action(
        action="system.demo.remove",
        user_id=current_user.get("id"),
        metadata=result,
        request=request,
    )
    return result
