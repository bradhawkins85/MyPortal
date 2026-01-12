from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import companies as company_repo
from app.repositories import shop as shop_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import users as users_repo
from app.schemas.quotes import QuoteAssignRequest, QuoteDetailResponse, QuoteSummaryResponse, QuoteUpdateRequest

router = APIRouter(prefix="/api/quotes", tags=["Quotes"])


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


async def _ensure_quote_access(user: dict, company_id: int, quote_summary: dict | None = None) -> None:
    """Ensure user has access to quotes for this company.
    
    Access is granted if:
    1. User is a super admin, OR
    2. User has can_access_quotes permission for the company, OR
    3. User is the creator of the quote (user_id matches), OR
    4. User is assigned to the quote (assigned_user_id matches)
    """
    if user.get("is_super_admin"):
        return
    
    user_id = user.get("id")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    
    # Check if user is the creator or assigned user (if quote_summary provided)
    if quote_summary:
        if quote_summary.get("user_id") == user_id or quote_summary.get("assigned_user_id") == user_id:
            return
    
    # Check company membership and permissions
    membership = await user_company_repo.get_user_company(int(user_id), company_id)
    if not membership or not bool(membership.get("can_access_quotes")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Quotes access denied")


async def _can_assign_quotes(user: dict, company_id: int) -> bool:
    """Check if user can assign quotes.
    
    Only super admins and users with is_admin permission for the company can assign quotes.
    """
    if user.get("is_super_admin"):
        return True
    
    user_id = user.get("id")
    if user_id is None:
        return False
    
    membership = await user_company_repo.get_user_company(int(user_id), company_id)
    if not membership:
        return False
    
    return bool(membership.get("is_admin"))


@router.get("", response_model=list[QuoteSummaryResponse])
async def list_quotes(
    company_id: int | None = Query(default=None, alias="companyId"),
    company_id_alt: int | None = Query(default=None, alias="company_id"),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> list[QuoteSummaryResponse]:
    resolved_company_id = await _resolve_company_id(company_id, company_id_alt)
    await _ensure_company(resolved_company_id)
    await _ensure_quote_access(current_user, resolved_company_id)
    records = await shop_repo.list_quote_summaries(resolved_company_id)
    return [QuoteSummaryResponse.model_validate(record) for record in records]


@router.get("/{quote_number}", response_model=QuoteDetailResponse)
async def get_quote(
    quote_number: str,
    company_id: int | None = Query(default=None, alias="companyId"),
    company_id_alt: int | None = Query(default=None, alias="company_id"),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> QuoteDetailResponse:
    resolved_company_id = await _resolve_company_id(company_id, company_id_alt)
    await _ensure_company(resolved_company_id)
    summary = await shop_repo.get_quote_summary(quote_number, resolved_company_id)
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found")
    await _ensure_quote_access(current_user, resolved_company_id, summary)
    items = await shop_repo.list_quote_items(quote_number, resolved_company_id)
    summary["items"] = items
    return QuoteDetailResponse.model_validate(summary)


@router.patch("/{quote_number}", response_model=QuoteDetailResponse)
async def update_quote(
    quote_number: str,
    payload: QuoteUpdateRequest,
    company_id: int | None = Query(default=None, alias="companyId"),
    company_id_alt: int | None = Query(default=None, alias="company_id"),
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> QuoteDetailResponse:
    resolved_company_id = await _resolve_company_id(company_id, company_id_alt)
    await _ensure_company(resolved_company_id)
    data = payload.model_dump(exclude_unset=True, by_alias=False)
    updates: dict[str, object] = {}
    if "status" in data:
        value = data["status"]
        updates["status"] = str(value).strip() if value is not None else "active"
    if "notes" in data:
        updates["notes"] = data["notes"]
    if "po_number" in data:
        value = data["po_number"]
        updates["po_number"] = value.strip() if isinstance(value, str) else value
    if "name" in data:
        value = data["name"]
        updates["name"] = value.strip() if isinstance(value, str) else value

    summary = await shop_repo.update_quote(quote_number, resolved_company_id, **updates)
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found")
    items = await shop_repo.list_quote_items(quote_number, resolved_company_id)
    summary["items"] = items
    return QuoteDetailResponse.model_validate(summary)


@router.delete("/{quote_number}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quote(
    quote_number: str,
    company_id: int | None = Query(default=None, alias="companyId"),
    company_id_alt: int | None = Query(default=None, alias="company_id"),
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    resolved_company_id = await _resolve_company_id(company_id, company_id_alt)
    await _ensure_company(resolved_company_id)
    summary = await shop_repo.get_quote_summary(quote_number, resolved_company_id)
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found")
    await shop_repo.delete_quote(quote_number, resolved_company_id)
    return None


@router.post("/{quote_number}/assign", response_model=QuoteDetailResponse)
async def assign_quote(
    quote_number: str,
    payload: QuoteAssignRequest,
    company_id: int | None = Query(default=None, alias="companyId"),
    company_id_alt: int | None = Query(default=None, alias="company_id"),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> QuoteDetailResponse:
    """Assign a quote to a specific user or unassign it.
    
    Only super admins and company admins can assign quotes.
    The assigned user must be a member of the company.
    """
    resolved_company_id = await _resolve_company_id(company_id, company_id_alt)
    await _ensure_company(resolved_company_id)
    
    # Check if user can assign quotes
    if not await _can_assign_quotes(current_user, resolved_company_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins and company admins can assign quotes"
        )
    
    # Check if quote exists
    summary = await shop_repo.get_quote_summary(quote_number, resolved_company_id)
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found")
    
    assigned_user_id = payload.assigned_user_id
    
    # If assigning to a user, verify they are a member of the company
    if assigned_user_id is not None:
        # Verify user exists
        assigned_user = await users_repo.get_user_by_id(assigned_user_id)
        if not assigned_user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        
        # Verify user has access to this company
        membership = await user_company_repo.get_user_company(assigned_user_id, resolved_company_id)
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User is not a member of this company"
            )
    
    # Assign the quote
    updated_summary = await shop_repo.assign_quote(quote_number, resolved_company_id, assigned_user_id)
    if not updated_summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found")
    
    # Return full quote details
    items = await shop_repo.list_quote_items(quote_number, resolved_company_id)
    updated_summary["items"] = items
    return QuoteDetailResponse.model_validate(updated_summary)
