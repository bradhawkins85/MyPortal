from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import assets as assets_repo
from app.repositories import companies as company_repo
from app.repositories import company_recurring_invoice_items as recurring_items_repo
from app.schemas.assets import AssetResponse
from app.schemas.companies import CompanyCreate, CompanyResponse, CompanyUpdate
from app.schemas.company_recurring_invoice_items import (
    RecurringInvoiceItemCreate,
    RecurringInvoiceItemResponse,
    RecurringInvoiceItemUpdate,
)
from app.services import company_id_lookup

router = APIRouter(prefix="/api/companies", tags=["Companies"])


@router.get("", response_model=list[CompanyResponse])
async def list_companies(
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    rows = await company_repo.list_companies()
    return rows


@router.post("", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
async def create_company(
    payload: CompanyCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    created = await company_repo.create_company(**payload.model_dump())
    company_id = created.get("id")
    
    # Lookup missing external IDs after creating the company
    if company_id:
        try:
            await company_id_lookup.lookup_missing_company_ids(company_id)
            # Fetch the updated company to return with any newly found IDs
            updated = await company_repo.get_company_by_id(company_id)
            if updated:
                return updated
        except Exception:
            # If lookup fails, still return the created company
            pass
    
    return created


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


@router.patch("/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: int,
    payload: CompanyUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    data = payload.model_dump(exclude_unset=True)
    updated = await company_repo.update_company(company_id, **data)
    
    # Check if any external IDs are missing and lookup if needed
    has_missing_ids = (
        not updated.get("syncro_company_id") or
        not updated.get("tacticalrmm_client_id") or
        not updated.get("xero_id")
    )
    
    if has_missing_ids:
        try:
            await company_id_lookup.lookup_missing_company_ids(company_id)
            # Fetch the updated company to return with any newly found IDs
            refreshed = await company_repo.get_company_by_id(company_id)
            if refreshed:
                return refreshed
        except Exception:
            # If lookup fails, still return the updated company
            pass
    
    return updated


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    await company_repo.delete_company(company_id)
    return None


@router.get("/{company_id}/assets", response_model=list[AssetResponse])
async def list_company_assets(
    company_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    rows = await assets_repo.list_company_assets(company_id)
    assets: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        for numeric_key in ("ram_gb", "approx_age", "performance_score"):
            value = record.get(numeric_key)
            if value is None or value == "":
                record[numeric_key] = None
                continue
            try:
                record[numeric_key] = float(value)
            except (TypeError, ValueError):
                record[numeric_key] = None
        assets.append(record)
    return assets


@router.get("/{company_id}/recurring-invoice-items", response_model=list[RecurringInvoiceItemResponse])
async def list_company_recurring_invoice_items(
    company_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    items = await recurring_items_repo.list_company_recurring_invoice_items(company_id)
    return items


@router.post(
    "/{company_id}/recurring-invoice-items",
    response_model=RecurringInvoiceItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_recurring_invoice_item(
    company_id: int,
    payload: RecurringInvoiceItemCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    item = await recurring_items_repo.create_recurring_invoice_item(
        company_id=company_id,
        **payload.model_dump(),
    )
    return item


@router.patch(
    "/{company_id}/recurring-invoice-items/{item_id}",
    response_model=RecurringInvoiceItemResponse,
)
async def update_recurring_invoice_item(
    company_id: int,
    item_id: int,
    payload: RecurringInvoiceItemUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    item = await recurring_items_repo.get_recurring_invoice_item(item_id)
    if not item or item.get("company_id") != company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurring invoice item not found")
    updated = await recurring_items_repo.update_recurring_invoice_item(
        item_id,
        **payload.model_dump(exclude_unset=True),
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurring invoice item not found")
    return updated


@router.delete("/{company_id}/recurring-invoice-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recurring_invoice_item(
    company_id: int,
    item_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    item = await recurring_items_repo.get_recurring_invoice_item(item_id)
    if not item or item.get("company_id") != company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurring invoice item not found")
    await recurring_items_repo.delete_recurring_invoice_item(item_id)
    return None


@router.post("/{company_id}/lookup-tactical-id")
async def lookup_tactical_rmm_client_id(
    company_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Lookup Tactical RMM client ID for a company by searching the API."""
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    
    company_name = company.get("name", "")
    if not company_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Company name is required")
    
    try:
        tactical_id = await company_id_lookup._lookup_tactical_client_id(company_name)
        if tactical_id:
            # Update the company with the found ID
            await company_repo.update_company(company_id, tacticalrmm_client_id=tactical_id)
            return {"status": "found", "id": tactical_id}
        else:
            return {"status": "not_found", "id": None}
    except company_id_lookup.tacticalrmm.TacticalRMMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Tactical RMM not configured: {str(exc)}"
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error looking up Tactical RMM client ID: {str(exc)}"
        )


@router.post("/{company_id}/lookup-xero-id")
async def lookup_xero_contact_id(
    company_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Lookup Xero contact ID for a company by searching the API."""
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    
    company_name = company.get("name", "")
    if not company_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Company name is required")
    
    try:
        xero_id = await company_id_lookup._lookup_xero_contact_id(company_name)
        if xero_id:
            # Update the company with the found ID
            await company_repo.update_company(company_id, xero_id=xero_id)
            return {"status": "found", "id": xero_id}
        else:
            return {"status": "not_found", "id": None}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error looking up Xero contact ID: {str(exc)}"
        )
