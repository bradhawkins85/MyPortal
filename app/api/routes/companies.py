from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import assets as assets_repo
from app.repositories import companies as company_repo
from app.schemas.assets import AssetResponse
from app.schemas.companies import CompanyCreate, CompanyResponse, CompanyUpdate

router = APIRouter(prefix="/companies", tags=["Companies"])


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
