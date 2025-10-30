from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import companies as company_repo
from app.repositories import shop as shop_repo
from app.repositories import user_companies as user_company_repo
from app.schemas.orders import OrderDetailResponse, OrderSummaryResponse, OrderUpdateRequest

router = APIRouter(prefix="/api/orders", tags=["Orders"])


async def _resolve_company_id(company_id: int | None, company_id_alt: int | None) -> int:
    value = company_id if company_id is not None else company_id_alt
    if value is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="companyId is required")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid companyId") from exc


async def _ensure_company(company_id: int) -> None:
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")


async def _ensure_order_access(user: dict, company_id: int) -> None:
    if user.get("is_super_admin"):
        return
    user_id = user.get("id")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    membership = await user_company_repo.get_user_company(int(user_id), company_id)
    if not membership or not bool(membership.get("can_access_orders")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Orders access denied")


@router.get("", response_model=list[OrderSummaryResponse])
async def list_orders(
    company_id: int | None = Query(default=None, alias="companyId"),
    company_id_alt: int | None = Query(default=None, alias="company_id"),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> list[OrderSummaryResponse]:
    resolved_company_id = await _resolve_company_id(company_id, company_id_alt)
    await _ensure_company(resolved_company_id)
    await _ensure_order_access(current_user, resolved_company_id)
    records = await shop_repo.list_order_summaries(resolved_company_id)
    return [OrderSummaryResponse.model_validate(record) for record in records]


@router.get("/{order_number}", response_model=OrderDetailResponse)
async def get_order(
    order_number: str,
    company_id: int | None = Query(default=None, alias="companyId"),
    company_id_alt: int | None = Query(default=None, alias="company_id"),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> OrderDetailResponse:
    resolved_company_id = await _resolve_company_id(company_id, company_id_alt)
    await _ensure_company(resolved_company_id)
    await _ensure_order_access(current_user, resolved_company_id)
    summary = await shop_repo.get_order_summary(order_number, resolved_company_id)
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    items = await shop_repo.list_order_items(order_number, resolved_company_id)
    summary["items"] = items
    return OrderDetailResponse.model_validate(summary)


@router.patch("/{order_number}", response_model=OrderDetailResponse)
async def update_order(
    order_number: str,
    payload: OrderUpdateRequest,
    company_id: int | None = Query(default=None, alias="companyId"),
    company_id_alt: int | None = Query(default=None, alias="company_id"),
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> OrderDetailResponse:
    resolved_company_id = await _resolve_company_id(company_id, company_id_alt)
    await _ensure_company(resolved_company_id)
    data = payload.model_dump(exclude_unset=True, by_alias=False)
    updates: dict[str, object] = {}
    if "status" in data:
        value = data["status"]
        updates["status"] = str(value).strip() if value is not None else "pending"
    if "shipping_status" in data:
        value = data["shipping_status"]
        updates["shipping_status"] = str(value).strip() if value is not None else "pending"
    if "notes" in data:
        updates["notes"] = data["notes"]
    if "po_number" in data:
        value = data["po_number"]
        updates["po_number"] = value.strip() if isinstance(value, str) else value
    if "consignment_id" in data:
        value = data["consignment_id"]
        updates["consignment_id"] = value.strip() if isinstance(value, str) else value
    if "eta" in data:
        updates["eta"] = data["eta"]

    summary = await shop_repo.update_order(order_number, resolved_company_id, **updates)
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    items = await shop_repo.list_order_items(order_number, resolved_company_id)
    summary["items"] = items
    return OrderDetailResponse.model_validate(summary)
