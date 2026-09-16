from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import companies as company_repo
from app.repositories import invoices as invoice_repo
from app.repositories import invoice_lines as invoice_lines_repo
from app.repositories import tickets as tickets_repo
from app.repositories import user_companies as user_company_repo
from app.schemas.invoices import InvoiceCreate, InvoiceResponse, InvoiceUpdate
from app.services import audit as audit_service
from app.services import invoice_generator as invoice_generator_service
from app.services import xero as xero_service
from app.services.module_gate import require_enabled

router = APIRouter(prefix="/api/invoices", tags=["Invoices"])


class InvoiceBillNowRequest(BaseModel):
    ticket_id: int = Field(alias="ticketId", gt=0)
    approval_required: bool = Field(default=True, alias="approvalRequired")

    model_config = {"populate_by_name": True}


class InvoiceBatchSyncRequest(BaseModel):
    company_id: int | None = Field(default=None, alias="companyId")
    invoice_ids: list[int] = Field(default_factory=list, alias="invoiceIds")
    auto_send: bool = Field(default=False, alias="autoSend")
    exception_only: bool = Field(default=False, alias="exceptionOnly")

    model_config = {"populate_by_name": True}


async def _sum_invoice_line_amounts(invoice_id: int) -> Decimal:
    total = Decimal("0.00")
    for line in await invoice_lines_repo.list_invoice_lines(invoice_id):
        amount_value = line.get("amount")
        if amount_value is None:
            quantity = Decimal(str(line.get("quantity") or "0"))
            unit_amount = Decimal(str(line.get("unit_amount") or "0"))
            amount_value = quantity * unit_amount
        total += amount_value if isinstance(amount_value, Decimal) else Decimal(str(amount_value))
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _apply_adjusted_invoice_total(
    invoice_id: int,
    existing: dict[str, Any],
    data: dict[str, Any],
) -> None:
    if not {
        "billing_adjustment_type",
        "billing_adjustment_amount",
        "billing_adjustment_reason",
    }.intersection(data):
        return
    if "amount" in data:
        return
    adjustment_value = data.get(
        "billing_adjustment_amount",
        existing.get("billing_adjustment_amount"),
    )
    adjustment_decimal = (
        adjustment_value
        if isinstance(adjustment_value, Decimal)
        else Decimal(str(adjustment_value or "0"))
    )
    data["amount"] = (
        await _sum_invoice_line_amounts(invoice_id) + adjustment_decimal
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _build_batch_dashboard(records: list[dict[str, Any]]) -> dict[str, Any]:
    unsynced = [record for record in records if not str(record.get("xero_invoice_id") or "").strip()]
    approval_queue = [
        record
        for record in unsynced
        if bool(record.get("approval_required")) and not record.get("approved_at")
    ]
    exception_queue = [
        record
        for record in unsynced
        if str(record.get("xero_sync_error") or "").strip()
    ]
    return {
        "invoiceCount": len(unsynced),
        "approvalQueueCount": len(approval_queue),
        "exceptionQueueCount": len(exception_queue),
        "exceptionQueue": [
            {
                "id": int(record["id"]),
                "companyId": int(record["company_id"]),
                "invoiceNumber": record.get("invoice_number"),
                "xeroSyncError": record.get("xero_sync_error"),
            }
            for record in exception_queue
            if record.get("id") is not None and record.get("company_id") is not None
        ],
    }


async def _ensure_company_access(user: dict, company_id: int) -> None:
    if user.get("is_super_admin"):
        return
    membership = await user_company_repo.get_user_company(user["id"], company_id)
    if not membership or not bool(membership.get("can_manage_invoices")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invoices access denied")


@router.get("", response_model=list[InvoiceResponse])
async def list_invoices(
    company_id: int | None = Query(default=None, alias="companyId"),
    company_id_alt: int | None = Query(default=None, alias="company_id"),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    if company_id is None:
        company_id = company_id_alt
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="companyId is required")
    company_id = int(company_id)
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    await _ensure_company_access(current_user, company_id)
    records = await invoice_repo.list_company_invoices(company_id)
    return [InvoiceResponse.model_validate(record) for record in records]


@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    payload: InvoiceCreate,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    company = await company_repo.get_company_by_id(payload.company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    created = await invoice_repo.create_invoice(
        company_id=payload.company_id,
        invoice_number=payload.invoice_number,
        amount=payload.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        due_date=payload.due_date,
        status=payload.status,
        approval_required=payload.approval_required,
        approved_by=payload.approved_by,
        approved_at=payload.approved_at,
        billing_adjustment_type=payload.billing_adjustment_type,
        billing_adjustment_amount=payload.billing_adjustment_amount,
        billing_adjustment_reason=payload.billing_adjustment_reason,
    )
    await audit_service.record(
        action="invoice.create",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="invoice",
        entity_id=int(created["id"]) if created.get("id") is not None else None,
        before=None,
        after=created,
        metadata={"company_id": payload.company_id},
    )
    return InvoiceResponse.model_validate(created)


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: int,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    invoice = await invoice_repo.get_invoice_by_id(invoice_id)
    if not invoice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    await _ensure_company_access(current_user, int(invoice["company_id"]))
    return InvoiceResponse.model_validate(invoice)


@router.put("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdate,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    existing = await invoice_repo.get_invoice_by_id(invoice_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    data = payload.model_dump(exclude_unset=True)
    merged = existing | data
    company = await company_repo.get_company_by_id(int(merged["company_id"]))
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    amount_value = merged.get("amount")
    if amount_value is None:
        amount_value = Decimal("0.00")
    amount_decimal = amount_value if isinstance(amount_value, Decimal) else Decimal(str(amount_value))
    amount_decimal = amount_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    await _apply_adjusted_invoice_total(invoice_id, existing, data)
    amount_decimal = data.get("amount", amount_decimal)
    updated = await invoice_repo.update_invoice(
        invoice_id,
        company_id=int(merged["company_id"]),
        invoice_number=str(merged["invoice_number"]),
        amount=amount_decimal,
        due_date=merged.get("due_date"),
        status=merged.get("status"),
    )
    adjustment_updates = {
        key: merged.get(key)
        for key in (
            "approval_required",
            "approved_by",
            "approved_at",
            "billing_adjustment_type",
            "billing_adjustment_amount",
            "billing_adjustment_reason",
            "xero_sync_error",
            "xero_sync_attempted_at",
        )
    }
    updated = await invoice_repo.patch_invoice(invoice_id, **adjustment_updates)
    await audit_service.record(
        action="invoice.update",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="invoice",
        entity_id=invoice_id,
        before=existing,
        after=updated,
        metadata={"company_id": int(merged["company_id"])},
    )
    return InvoiceResponse.model_validate(updated)


@router.patch("/{invoice_id}", response_model=InvoiceResponse)
async def patch_invoice(
    invoice_id: int,
    payload: InvoiceUpdate,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    existing = await invoice_repo.get_invoice_by_id(invoice_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    data = payload.model_dump(exclude_unset=True)
    if "company_id" in data:
        company = await company_repo.get_company_by_id(int(data["company_id"]))
        if not company:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if "amount" in data:
        amount_value = data["amount"]
        amount_decimal = amount_value if isinstance(amount_value, Decimal) else Decimal(str(amount_value))
        data["amount"] = amount_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    await _apply_adjusted_invoice_total(invoice_id, existing, data)
    updated = await invoice_repo.patch_invoice(invoice_id, **data)
    company_id_value = updated.get("company_id") or existing.get("company_id")
    audit_metadata = {"company_id": int(company_id_value)} if company_id_value is not None else None
    await audit_service.record(
        action="invoice.update",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="invoice",
        entity_id=invoice_id,
        before=existing,
        after=updated,
        metadata=audit_metadata,
    )
    return InvoiceResponse.model_validate(updated)


@router.post("/{invoice_id}/xero/sync")
async def sync_invoice_to_xero(
    invoice_id: int,
    request: Request,
    auto_send: bool = Query(default=False, alias="autoSend"),
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    await require_enabled("xero")
    existing = await invoice_repo.get_invoice_by_id(invoice_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    result = await xero_service.sync_invoice(invoice_id, auto_send=auto_send)
    if result.get("status") == "skipped" and result.get("reason") == "Invoice requires approval before Xero sync":
        await audit_service.record(
            action="invoice.xero_sync_blocked",
            request=request,
            user_id=int(current_user["id"]),
            entity_type="invoice",
            entity_id=invoice_id,
            before=existing,
            after=None,
            metadata={"company_id": int(existing["company_id"]), "result": result},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=result,
        )
    if result.get("status") in {"failed", "error"}:
        await audit_service.record(
            action="invoice.xero_sync_failed",
            request=request,
            user_id=int(current_user["id"]),
            entity_type="invoice",
            entity_id=invoice_id,
            before=existing,
            after=None,
            metadata={"company_id": int(existing["company_id"]), "result": result},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to synchronise invoice with Xero",
        )
    updated = await invoice_repo.get_invoice_by_id(invoice_id)
    await audit_service.record(
        action="invoice.xero_sync",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="invoice",
        entity_id=invoice_id,
        before=existing,
        after=updated,
        metadata={"company_id": int(existing["company_id"]), "result": result},
    )
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "invoice_id": result.get("invoice_id", invoice_id),
        "xero_invoice_id": result.get("xero_invoice_id"),
        "contact_lookup": result.get("contact_lookup"),
    }


@router.post("/bill-now", status_code=status.HTTP_201_CREATED)
async def bill_ticket_now(
    payload: InvoiceBillNowRequest,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    await require_enabled("xero")
    ticket = await tickets_repo.get_ticket(payload.ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    if ticket.get("xero_invoice_number"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ticket is already billed",
        )
    company_id = ticket.get("company_id")
    try:
        company_id_int = int(company_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ticket is missing a valid company",
        ) from exc
    result = await invoice_generator_service.generate_invoice(
        company_id_int,
        ticket_ids=[payload.ticket_id],
        include_recurring_items=False,
        approval_required=payload.approval_required,
    )
    if result.get("status") != "succeeded":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result)
    created = await invoice_repo.get_invoice_by_id(int(result["invoice_id"]))
    await audit_service.record(
        action="invoice.bill_now",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="invoice",
        entity_id=int(result["invoice_id"]),
        before=None,
        after=created,
        metadata={"company_id": company_id_int, "ticket_id": payload.ticket_id},
    )
    return {
        **result,
        "redirectUrl": f"/invoices/{result['invoice_id']}",
    }


@router.get("/{invoice_id}/xero/preview")
async def preview_invoice_for_xero(
    invoice_id: int,
    auto_send: bool = Query(default=False, alias="autoSend"),
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    del current_user
    await require_enabled("xero")
    result = await xero_service.preview_invoice_sync(invoice_id, auto_send=auto_send)
    if result.get("status") == "skipped":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result)
    return result


@router.post("/{invoice_id}/xero/approve", response_model=InvoiceResponse)
async def approve_invoice_for_xero(
    invoice_id: int,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    existing = await invoice_repo.get_invoice_by_id(invoice_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if str(existing.get("xero_invoice_id") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invoice is already linked to Xero",
        )
    updated = await invoice_repo.patch_invoice(
        invoice_id,
        approval_required=True,
        approved_by=int(current_user["id"]),
        approved_at=datetime.now(timezone.utc),
        status="approved",
        xero_sync_error=None,
    )
    await audit_service.record(
        action="invoice.xero_approve",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="invoice",
        entity_id=invoice_id,
        before=existing,
        after=updated,
        metadata={"company_id": int(existing["company_id"])},
    )
    return InvoiceResponse.model_validate(updated)


@router.get("/xero/batch-dashboard")
async def get_xero_batch_dashboard(
    company_id: int | None = Query(default=None, alias="companyId"),
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    del current_user
    records = (
        await invoice_repo.list_company_invoices(int(company_id))
        if company_id is not None
        else await invoice_repo.list_all_invoices()
    )
    return _build_batch_dashboard(records)


@router.post("/xero/batch-sync")
async def run_xero_batch_sync(
    payload: InvoiceBatchSyncRequest,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    await require_enabled("xero")
    if payload.company_id is not None:
        records = await invoice_repo.list_company_invoices(int(payload.company_id))
    else:
        records = await invoice_repo.list_all_invoices()
    requested_ids = {int(invoice_id) for invoice_id in payload.invoice_ids if invoice_id}
    candidates = [
        record
        for record in records
        if not str(record.get("xero_invoice_id") or "").strip()
        and (not payload.exception_only or str(record.get("xero_sync_error") or "").strip())
        and (not requested_ids or int(record.get("id") or 0) in requested_ids)
    ]
    grouped: dict[int, list[int]] = {}
    for record in candidates:
        company_id_value = record.get("company_id")
        invoice_id_value = record.get("id")
        if company_id_value is None or invoice_id_value is None:
            continue
        grouped.setdefault(int(company_id_value), []).append(int(invoice_id_value))
    results = []
    for company_id_value, invoice_ids in grouped.items():
        results.append(
            await xero_service.sync_company(
                company_id_value,
                auto_send=payload.auto_send,
                invoice_ids=invoice_ids,
            )
        )
    refreshed = (
        await invoice_repo.list_company_invoices(int(payload.company_id))
        if payload.company_id is not None
        else await invoice_repo.list_all_invoices()
    )
    summary = {
        "status": "skipped" if not results else "succeeded",
        "companyCount": len(grouped),
        "invoiceCount": len(candidates),
        "syncedCount": sum(int(result.get("synced_count") or 0) for result in results),
        "failedCount": sum(int(result.get("failed_count") or 0) for result in results),
        "skippedCount": sum(int(result.get("skipped_count") or 0) for result in results),
        "results": results,
        "dashboard": _build_batch_dashboard(refreshed),
    }
    if summary["failedCount"] and summary["syncedCount"]:
        summary["status"] = "partial"
    elif summary["failedCount"]:
        summary["status"] = "failed"
    await audit_service.record(
        action="invoice.xero_batch_sync",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="invoice_batch",
        entity_id=None,
        before=None,
        after=None,
        metadata=summary,
    )
    return summary


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(
    invoice_id: int,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    existing = await invoice_repo.get_invoice_by_id(invoice_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    await invoice_repo.delete_invoice(invoice_id)
    await audit_service.record(
        action="invoice.delete",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="invoice",
        entity_id=invoice_id,
        before=existing,
        after=None,
        metadata={"company_id": int(existing.get("company_id"))} if existing.get("company_id") is not None else None,
    )
    return None
