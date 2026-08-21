"""Super-administrator provisioning and diagnostic routes."""

from __future__ import annotations
from pathlib import Path
from typing import Annotated
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.modules import require_module_enabled
from app.repositories import voice_monitor as repo
from app.repositories import companies as company_repo
from app.repositories import subscriptions as subscriptions_repo
from app.schemas.voice_monitor import VoiceMonitorConfiguration
from app.services import audit as audit_service
from app.services.voice_monitor_billing import (
    is_voice_monitor_subscription,
    list_voice_monitor_products,
    provision_subscription,
)

router = APIRouter(
    prefix="/admin/voice-monitor",
    tags=["Voice Monitor Admin"],
    dependencies=[Depends(require_module_enabled("voice-monitor"))],
)


def _main():
    from app import main

    return main


class ProviderConfiguration(BaseModel):
    provider_type: str = Field(pattern="^(disabled|sip|twilio|telnyx|mock)$")
    endpoint: str = ""
    credentials_encrypted: str = ""
    caller_identity: str = ""
    per_user_hourly_limit: int = Field(3, ge=1, le=100)
    per_company_hourly_limit: int = Field(10, ge=1, le=1000)
    recording_retention_days: int = Field(30, ge=0, le=3650)
    worker_concurrency: int = Field(5, ge=1, le=100)
    worker_lease_seconds: int = Field(300, ge=30, le=3600)
    test_calls_enabled: bool = False
    allowed_country_codes: list[int] = Field(default_factory=list, max_length=50)
    global_call_concurrency: int = Field(10, ge=1, le=1000)
    tenant_call_concurrency: int = Field(2, ge=1, le=100)
    daily_attempt_limit: int = Field(10, ge=1, le=10000)
    retry_ceiling: int = Field(2, ge=0, le=20)
    monetary_cap_minor: int = Field(1000, ge=1)


class ManualCall(BaseModel):
    company_id: int = Field(gt=0)
    endpoint_id: int = Field(gt=0)


@router.get("", response_class=HTMLResponse)
async def page(request: Request):
    return await _render_management_page(request, subscriptions_page=False)


@router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request):
    """Render Voice Monitor's monitored-number subscription management page."""
    return await _render_management_page(request, subscriptions_page=True)


async def _render_management_page(request: Request, *, subscriptions_page: bool):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    companies = await company_repo.list_companies()
    subscriptions = await subscriptions_repo.list_subscriptions(
        status="active", limit=500
    )
    voice_monitor_subscriptions = [
        subscription
        for subscription in subscriptions
        if is_voice_monitor_subscription(subscription)
    ]
    voice_monitor_products = await list_voice_monitor_products()
    return await _main()._render_template(
        "admin/voice_monitor.html",
        request,
        user,
        extra={
            "title": (
                "Manage Voice Monitor Subscriptions"
                if subscriptions_page
                else "Voice Monitor"
            ),
            "companies": companies,
            "voice_monitor_subscriptions": voice_monitor_subscriptions,
            "voice_monitor_products": voice_monitor_products,
            "subscriptions_page": subscriptions_page,
        },
    )


@router.post("/endpoints", status_code=201)
async def provision_endpoint(
    company_id: int,
    payload: VoiceMonitorConfiguration,
    request: Request,
    user: dict = Depends(require_super_admin),
    product_id: int | None = None,
):
    if payload.subscription_id is None and product_id is None:
        raise HTTPException(
            400,
            "Either subscription_id in the request body or product_id as a query "
            "parameter is required to provision a monitored number.",
        )
    values = payload.model_dump(mode="json")
    values["consent_actor_id"] = int(user["id"])
    if payload.subscription_id is None and product_id is not None:
        try:
            subscription = await provision_subscription(
                company_id, product_id, created_by=int(user["id"])
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        values["subscription_id"] = subscription["id"]
        await audit_service.log_action(
            action="voice_monitor.subscription.provisioned",
            user_id=user["id"],
            entity_type="subscription",
            entity_id=subscription["id"],
            new_value={"product_id": product_id, "company_id": company_id},
            request=request,
        )
    try:
        endpoint = await repo.create_endpoint(company_id, values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OverflowError as exc:
        raise HTTPException(409, str(exc)) from exc
    await audit_service.log_action(
        action="voice_monitor.endpoint.created",
        user_id=user["id"],
        entity_type="voice_monitor_endpoint",
        entity_id=endpoint["id"],
        new_value=endpoint,
        metadata={"company_id": company_id},
        request=request,
    )
    return endpoint


@router.get("/attempts/{company_id}/{attempt_id}")
async def diagnostics(
    company_id: int, attempt_id: int, user: dict = Depends(require_super_admin)
):
    attempt = await repo.get_attempt(company_id, attempt_id)
    if not attempt:
        raise HTTPException(404, "Attempt not found")
    return attempt


@router.get("/worker-health")
async def worker_health(user: dict = Depends(require_super_admin)):
    path = Path("/run/myportal/voice-monitor.health")
    return {
        "healthy": path.exists(),
        "heartbeat": path.read_text()[:1000] if path.exists() else None,
        "metrics": await repo.operational_metrics(),
    }


@router.put("/provider")
async def provider_configuration(
    payload: ProviderConfiguration,
    request: Request,
    user: dict = Depends(require_super_admin),
):
    # The generic module service applies its normal encryption/redaction rules;
    # this response never echoes credential ciphertext.
    from app.services import modules

    current = await modules.get_module("voice-monitor", redact=False)
    updated = await modules.update_module(
        "voice-monitor", settings=payload.model_dump()
    )
    await audit_service.log_action(
        action="voice_monitor.provider.updated",
        user_id=user["id"],
        entity_type="integration_module",
        previous_value=current,
        new_value={"settings": payload.model_dump(exclude={"credentials_encrypted"})},
        request=request,
    )
    return {
        **updated,
        "settings": {**(updated.get("settings") or {}), "credentials_encrypted": "***"},
    }


@router.post("/manual-calls", status_code=201)
async def manual_call(
    payload: ManualCall,
    request: Request,
    token: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    user: dict = Depends(require_super_admin),
    module: dict = Depends(require_module_enabled("voice-monitor")),
):
    if not token or len(token) > 128:
        raise HTTPException(400, "A valid Idempotency-Key header is required")
    settings = module.get("settings") or {}
    try:
        attempt, created = await repo.create_manual_attempt(
            payload.company_id,
            payload.endpoint_id,
            int(user["id"]),
            token,
            user_limit=int(settings.get("per_user_hourly_limit", 3)),
            company_limit=int(settings.get("per_company_hourly_limit", 10)),
        )
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc
    except OverflowError as exc:
        raise HTTPException(429, str(exc)) from exc
    if created:
        await audit_service.log_action(
            action="voice_monitor.manual_call.requested",
            user_id=user["id"],
            entity_type="voice_monitor_attempt",
            entity_id=attempt.get("id"),
            metadata={
                "company_id": payload.company_id,
                "endpoint_id": payload.endpoint_id,
            },
            request=request,
        )
    return {"attempt": attempt, "created": created}
