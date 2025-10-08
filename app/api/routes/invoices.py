from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import companies as company_repo
from app.repositories import invoices as invoice_repo
from app.repositories import user_companies as user_company_repo
from app.schemas.invoices import InvoiceCreate, InvoiceResponse, InvoiceUpdate

router = APIRouter(prefix="/api/invoices", tags=["Invoices"])


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
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
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
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
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
    updated = await invoice_repo.update_invoice(
        invoice_id,
        company_id=int(merged["company_id"]),
        invoice_number=str(merged["invoice_number"]),
        amount=amount_decimal,
        due_date=merged.get("due_date"),
        status=merged.get("status"),
    )
    return InvoiceResponse.model_validate(updated)


@router.patch("/{invoice_id}", response_model=InvoiceResponse)
async def patch_invoice(
    invoice_id: int,
    payload: InvoiceUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
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
    updated = await invoice_repo.patch_invoice(invoice_id, **data)
    return InvoiceResponse.model_validate(updated)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(
    invoice_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    existing = await invoice_repo.get_invoice_by_id(invoice_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    await invoice_repo.delete_invoice(invoice_id)
    return None
