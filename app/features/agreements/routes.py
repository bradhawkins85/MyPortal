"""Routes for service contracts and agreements (:mod:`app.services.service_agreements`)."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.datastructures import ImmutableMultiDict

from app.api.dependencies.auth import require_helpdesk_technician, require_super_admin
from app.repositories import companies as company_repo
from app.repositories import labour_types as labour_types_repo
from app.repositories import service_agreements as repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import service_agreements as agreements

router = APIRouter(tags=["Service agreements"])

PAGE_PATH = "/admin/agreements"


def _main():
    from app import main as main_module

    return main_module


def _optional_int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _audit_values(values: dict[str, Any]) -> dict[str, Any]:
    return {key: (str(value) if value is not None and not isinstance(value, (int, float, bool, list, str)) else value)
            for key, value in values.items()}


async def _labour_types() -> list[dict[str, Any]]:
    return [dict(item) for item in await labour_types_repo.list_labour_types()]


async def _render_list(request: Request, user: dict[str, Any], *, company_id: int | None,
                       editing: dict[str, Any] | None = None, errors: dict[str, str] | None = None,
                       status_code: int = status.HTTP_200_OK):
    items = await agreements.list_summaries(company_id=company_id)
    counts = {
        "active": sum(1 for item in items if item["status"] in ("active", "expiring")),
        "near_limit": sum(1 for item in items if "near_limit" in item["flags"]),
        "over": sum(1 for item in items if "exhausted" in item["flags"] or "overage" in item["flags"]),
        "expiring": sum(1 for item in items if item["status"] == "expiring"),
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(str(item.get("company_name") or "No company"), []).append(item)
    labour_types = await _labour_types()
    response = await _main()._render_template(
        "admin/agreements.html",
        request,
        user,
        extra={
            "title": "Service agreements",
            "groups": groups,
            "count": len(items),
            "counts": counts,
            "companies": await company_repo.list_companies(),
            "selected_company_id": company_id,
            "labour_types": labour_types,
            "labour_names": {int(item["id"]): item["name"] for item in labour_types},
            "agreement_types": agreements.AGREEMENT_TYPES,
            "periods": agreements.PERIODS,
            "can_manage": bool(user.get("is_super_admin")),
            "editing": editing,
            "errors": errors or {},
            "today": date.today().isoformat(),
        },
    )
    response.status_code = status_code
    return response


@router.get(PAGE_PATH, response_class=HTMLResponse, summary="Service agreements")
async def agreements_page(request: Request, company_id: str | None = Query(default=None),
                          agreement: int | None = Query(default=None, ge=1), new: bool = Query(default=False)):
    user, redirect = await _main()._require_helpdesk_page(request)
    if redirect:
        return redirect
    editing = None
    if user.get("is_super_admin") and agreement is not None:
        record = await repo.get_agreement(agreement)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agreement not found")
        editing = _editor_state(record)
    elif user.get("is_super_admin") and new:
        editing = {"id": None, "agreement_type": "hours", "period": "monthly", "alert_percent": 80,
                   "company_id": _optional_int(company_id), "labour_type_ids": [], "start_date": date.today().isoformat()}
    return await _render_list(request, user, company_id=_optional_int(company_id), editing=editing)


def _editor_state(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **record,
        "hours_allowance": f"{record['hours_allowance']:g}" if record.get("hours_allowance") is not None else "",
        "fee_amount": f"{record['fee_amount']:.2f}" if record.get("fee_amount") is not None else "",
        "start_date": record["start_date"].isoformat() if record.get("start_date") else "",
        "end_date": record["end_date"].isoformat() if record.get("end_date") else "",
    }


def _posted_state(form: Any, agreement_id: int | None) -> dict[str, Any]:
    return {
        "id": agreement_id, "company_id": _optional_int(form.get("company_id")), "name": form.get("name") or "",
        "agreement_type": form.get("agreement_type") or "hours", "hours_allowance": form.get("hours_allowance") or "",
        "period": form.get("period") or "monthly", "start_date": form.get("start_date") or "",
        "end_date": form.get("end_date") or "", "fee_amount": form.get("fee_amount") or "",
        "alert_percent": form.get("alert_percent") or 80, "notes": form.get("notes") or "",
        "is_cancelled": bool(form.get("is_cancelled")),
        "labour_type_ids": [value for value in (_optional_int(item) for item in form.getlist("labour_type_ids")) if value],
    }


async def _validated(form: Any) -> dict[str, Any]:
    labour_ids = [int(item["id"]) for item in await _labour_types()]
    values = agreements.parse_form(form, valid_labour_type_ids=labour_ids)
    company_id = _optional_int(form.get("company_id"))
    company = await company_repo.get_company_by_id(company_id) if company_id else None
    if not company:
        raise agreements.AgreementError({"company_id": "Choose the company the agreement is with."})
    values["company_id"] = company_id
    return values


async def _save(request: Request, agreement_id: int | None):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    previous = await repo.get_agreement(agreement_id) if agreement_id is not None else None
    if agreement_id is not None and previous is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agreement not found")
    form = await request.form()
    try:
        values = await _validated(form)
    except agreements.AgreementError as exc:
        return await _render_list(request, user, company_id=None, editing=_posted_state(form, agreement_id),
                                  errors=exc.errors, status_code=status.HTTP_400_BAD_REQUEST)
    if agreement_id is None:
        agreement_id = await repo.create_agreement(values, user_id=user.get("id"))
    else:
        await repo.update_agreement(agreement_id, values)
    await audit_service.log_action(
        action="agreement.save", user_id=user.get("id"), entity_type="service_agreement", entity_id=agreement_id,
        previous_value=_audit_values(previous) if previous else None, new_value=_audit_values(values), request=request,
    )
    return flash_redirect(f"{PAGE_PATH}/{agreement_id}", f"Saved {values['name']}.", "success")


@router.post(PAGE_PATH, response_class=HTMLResponse, summary="Add a service agreement")
async def create_agreement(request: Request):
    return await _save(request, None)


@router.post(PAGE_PATH + "/{agreement_id:int}", response_class=HTMLResponse, summary="Update a service agreement")
async def update_agreement(agreement_id: int, request: Request):
    return await _save(request, agreement_id)


@router.post(PAGE_PATH + "/{agreement_id:int}/delete", response_class=HTMLResponse, summary="Delete a service agreement")
async def delete_agreement(agreement_id: int, request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    record = await repo.get_agreement(agreement_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agreement not found")
    await repo.delete_agreement(agreement_id)
    await audit_service.log_action(action="agreement.delete", user_id=user.get("id"), entity_type="service_agreement",
                                   entity_id=agreement_id, previous_value=_audit_values(record), request=request)
    return flash_redirect(PAGE_PATH, f"Deleted {record['name']}.", "success")


@router.get(PAGE_PATH + "/{agreement_id:int}", response_class=HTMLResponse, summary="A service agreement's usage")
async def agreement_page(agreement_id: int, request: Request):
    user, redirect = await _main()._require_helpdesk_page(request)
    if redirect:
        return redirect
    record = await repo.get_agreement(agreement_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agreement not found")
    detail = await agreements.agreement_detail(record)
    labour_types = await _labour_types()
    return await _main()._render_template(
        "admin/agreement_detail.html",
        request,
        user,
        extra={
            "title": record["name"],
            "detail": detail,
            "labour_names": {int(item["id"]): item["name"] for item in labour_types},
            "description": agreements.describe(record, {int(item["id"]): item["name"] for item in labour_types}),
            "can_manage": bool(user.get("is_super_admin")),
            "hours_text": agreements.hours_text,
        },
    )


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


class AgreementIn(BaseModel):
    company_id: int
    name: str = Field(min_length=1, max_length=255)
    agreement_type: str = Field(default="hours", description="unlimited, hours or block")
    hours_allowance: float | None = Field(default=None, gt=0, le=agreements.MAX_HOURS)
    period: str = Field(default="monthly", description="monthly, quarterly or annual (hours agreements)")
    start_date: date
    end_date: date | None = None
    fee_amount: float | None = Field(default=None, ge=0)
    alert_percent: int = Field(default=80, ge=1, le=100)
    labour_type_ids: list[int] = Field(default_factory=list, description="Covered labour types; empty covers all")
    notes: str | None = Field(default=None, max_length=4000)
    is_cancelled: bool = False


def _api_form(payload: AgreementIn) -> ImmutableMultiDict:
    items: list[tuple[str, str]] = []
    for key, value in payload.model_dump().items():
        if key == "labour_type_ids":
            items.extend(("labour_type_ids", str(item)) for item in value)
        elif key == "is_cancelled":
            items.append((key, "1" if value else ""))
        elif value is not None:
            items.append((key, value.isoformat() if isinstance(value, date) else str(value)))
    return ImmutableMultiDict(items)


def _api_summary(item: dict[str, Any]) -> dict[str, Any]:
    usage = item["usage"]
    return {
        "id": item["id"], "company_id": item["company_id"], "company_name": item.get("company_name"),
        "name": item["name"], "agreement_type": item["agreement_type"], "period": item["period"],
        "hours_allowance": item.get("hours_allowance"), "start_date": item["start_date"].isoformat(),
        "end_date": item["end_date"].isoformat() if item.get("end_date") else None,
        "fee_amount": item.get("fee_amount"), "alert_percent": item.get("alert_percent"),
        "labour_type_ids": item.get("labour_type_ids") or [], "status": item["status"], "flags": item["flags"],
        "current_period": {
            "start": usage.period_start.isoformat(), "end": item["period_end"].isoformat() if item["period_end"] else None,
            "covered_minutes": usage.covered_minutes, "overage_minutes": usage.overage_minutes,
            "allowance_minutes": usage.allowance_minutes, "remaining_minutes": usage.remaining_minutes,
            "percent": usage.percent, "covered_value": item["value"], "effective_rate": item["effective_rate"],
        },
    }


@router.get("/api/agreements", summary="Service agreements with current usage")
async def api_list(company_id: int | None = Query(default=None),
                   _: dict = Depends(require_helpdesk_technician)) -> list[dict[str, Any]]:
    return [_api_summary(item) for item in await agreements.list_summaries(company_id=company_id)]


@router.get("/api/agreements/{agreement_id}", summary="A service agreement with its usage history")
async def api_get(agreement_id: int, _: dict = Depends(require_helpdesk_technician)) -> dict[str, Any]:
    record = await repo.get_agreement(agreement_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agreement not found")
    detail = await agreements.agreement_detail(record)
    payload = _api_summary(detail["agreement"])
    payload["history"] = [{**item, "start": item["start"].isoformat(),
                           "end": item["end"].isoformat() if item["end"] else None} for item in detail["history"]]
    return payload


async def _api_save(payload: AgreementIn, request: Request, user: dict, agreement_id: int | None) -> dict[str, Any]:
    previous = await repo.get_agreement(agreement_id) if agreement_id is not None else None
    if agreement_id is not None and previous is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agreement not found")
    try:
        values = await _validated(_api_form(payload))
    except agreements.AgreementError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    if agreement_id is None:
        agreement_id = await repo.create_agreement(values, user_id=user.get("id"))
    else:
        await repo.update_agreement(agreement_id, values)
    await audit_service.log_action(
        action="agreement.save", user_id=user.get("id"), entity_type="service_agreement", entity_id=agreement_id,
        previous_value=_audit_values(previous) if previous else None, new_value=_audit_values(values), request=request,
    )
    return await api_get(agreement_id, user)


@router.post("/api/agreements", summary="Add a service agreement", status_code=status.HTTP_201_CREATED)
async def api_create(payload: AgreementIn, request: Request, user: dict = Depends(require_super_admin)) -> dict[str, Any]:
    return await _api_save(payload, request, user, None)


@router.put("/api/agreements/{agreement_id}", summary="Update a service agreement")
async def api_update(agreement_id: int, payload: AgreementIn, request: Request,
                     user: dict = Depends(require_super_admin)) -> dict[str, Any]:
    return await _api_save(payload, request, user, agreement_id)


@router.delete("/api/agreements/{agreement_id}", summary="Delete a service agreement",
               status_code=status.HTTP_204_NO_CONTENT)
async def api_delete(agreement_id: int, request: Request, user: dict = Depends(require_super_admin)) -> None:
    record = await repo.get_agreement(agreement_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agreement not found")
    await repo.delete_agreement(agreement_id)
    await audit_service.log_action(action="agreement.delete", user_id=user.get("id"), entity_type="service_agreement",
                                   entity_id=agreement_id, previous_value=_audit_values(record), request=request)


__all__ = ["router"]
