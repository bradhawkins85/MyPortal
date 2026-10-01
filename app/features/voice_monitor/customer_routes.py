"""Customer voice-monitor pages and mutations; every query uses the active tenant."""

from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from app.api.dependencies.modules import require_module_enabled
from app.repositories import voice_monitor as repo
from app.security.rate_limiter import SimpleRateLimiter
from app.services import audit as audit_service

router = APIRouter(
    prefix="/voice-monitor",
    tags=["Voice Monitor"],
    dependencies=[Depends(require_module_enabled("voice-monitor"))],
)
limiter = SimpleRateLimiter(10, 3600, namespace="voice-monitor-manual")


def _main():
    from app import main

    return main


async def _customer(request: Request, *, write: bool = False):
    user, redirect = await _main()._require_menu_page_access(
        request, "menu.voice_monitor", write=write
    )
    if redirect:
        return None, None, redirect
    company_id = getattr(request.state, "active_company_id", None) or user.get(
        "company_id"
    )
    membership = getattr(request.state, "active_membership", None)
    if not company_id or (not user.get("is_super_admin") and not membership):
        raise HTTPException(403, "Active company membership required")
    return user, int(company_id), None


class Preferences(BaseModel):
    allow_test_calls: bool = False
    recording_enabled: bool = False
    notify_on_failure: bool = True


class TestCall(BaseModel):
    endpoint_id: int = Field(gt=0)


@router.get("", response_class=HTMLResponse)
async def index(request: Request):
    user, company_id, redirect = await _customer(request)
    if redirect:
        return redirect
    endpoints = await repo.list_endpoints(company_id)
    attempts = await repo.list_attempts(company_id, limit=25)
    return await _main()._render_template(
        "voice_monitor/index.html",
        request,
        user,
        extra={
            "title": "Voice Monitor",
            "endpoints": endpoints,
            "attempts": attempts,
            "preferences": await repo.get_preferences(company_id),
        },
    )


@router.put("/preferences")
async def preferences(payload: Preferences, request: Request):
    user, company_id, _ = await _customer(request, write=True)
    result = await repo.set_preferences(
        company_id, int(user["id"]), payload.model_dump()
    )
    await audit_service.log_action(
        action="voice_monitor.preferences.updated",
        user_id=user["id"],
        entity_type="company",
        entity_id=company_id,
        new_value=result,
        metadata={"company_id": company_id},
        request=request,
    )
    return result


@router.post("/test-calls", status_code=201)
async def test_call(
    payload: TestCall,
    request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    module: dict = Depends(require_module_enabled("voice-monitor")),
):
    user, company_id, _ = await _customer(request, write=True)
    prefs = await repo.get_preferences(company_id)
    settings = module.get("settings") or {}
    if not prefs.get("allow_test_calls") or not settings.get("test_calls_enabled"):
        raise HTTPException(403, "Test calls are not permitted")
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(400, "A valid Idempotency-Key header is required")
    allowed, retry = await limiter.check(f"{company_id}:{user['id']}")
    if not allowed:
        raise HTTPException(
            429,
            "Manual call rate limit exceeded",
            headers={"Retry-After": str(int(retry or 1))},
        )
    try:
        attempt, created = await repo.create_manual_attempt(
            company_id,
            payload.endpoint_id,
            int(user["id"]),
            idempotency_key,
            user_limit=int(settings.get("per_user_hourly_limit", 3)),
            company_limit=int(settings.get("per_company_hourly_limit", 10)),
        )
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc
    except OverflowError as exc:
        raise HTTPException(429, str(exc)) from exc
    if created:
        await audit_service.log_action(
            action="voice_monitor.test_call.requested",
            user_id=user["id"],
            entity_type="voice_monitor_attempt",
            entity_id=attempt.get("id"),
            metadata={"company_id": company_id, "endpoint_id": payload.endpoint_id},
            request=request,
        )
    return {"attempt": attempt, "created": created}
