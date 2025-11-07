"""API routes for managing subscriptions."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.repositories import subscriptions as subscriptions_repo
from app.repositories import user_companies as user_company_repo

router = APIRouter(prefix="/api/v1/subscriptions", tags=["Subscriptions"])


class SubscriptionResponse(BaseModel):
    """Response model for a subscription."""
    
    id: str
    customer_id: int = Field(..., alias="customerId")
    product_id: int = Field(..., alias="productId")
    product_name: str | None = Field(None, alias="productName")
    subscription_category_id: int | None = Field(None, alias="subscriptionCategoryId")
    category_name: str | None = Field(None, alias="categoryName")
    start_date: date = Field(..., alias="startDate")
    end_date: date = Field(..., alias="endDate")
    quantity: int
    unit_price: str = Field(..., alias="unitPrice")  # Decimal as string for JSON
    prorated_price: str | None = Field(None, alias="proratedPrice")
    status: str
    auto_renew: bool = Field(..., alias="autoRenew")
    created_at: str | None = Field(None, alias="createdAt")
    updated_at: str | None = Field(None, alias="updatedAt")
    
    class Config:
        populate_by_name = True


async def _ensure_subscription_access(user: dict, customer_id: int) -> None:
    """Ensure user has access to subscriptions for the given customer/company."""
    # Super admins have access to everything
    if user.get("is_super_admin"):
        return
    
    user_id = user.get("id")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="User not authenticated"
        )
    
    # Check if user is a company admin with License AND Cart permissions
    membership = await user_company_repo.get_user_company(int(user_id), customer_id)
    
    if not membership or not membership.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required"
        )
    
    has_license = bool(membership.get("can_manage_licenses"))
    has_cart = bool(membership.get("can_access_cart"))
    
    if not (has_license and has_cart):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="License and Cart permissions required to access subscriptions"
        )


@router.get("", response_model=list[SubscriptionResponse])
async def list_subscriptions(
    customer_id: int | None = Query(default=None, alias="customerId"),
    status: str | None = Query(default=None),
    category_id: int | None = Query(default=None, alias="categoryId"),
    end_before: date | None = Query(default=None, alias="endBefore"),
    end_after: date | None = Query(default=None, alias="endAfter"),
    limit: int = Query(default=100, ge=1, le=500),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> list[SubscriptionResponse]:
    """List subscriptions with optional filters.
    
    Requires:
    - User must be company admin
    - User must have both License and Cart permissions
    """
    # If customer_id not provided, we can't determine access
    if customer_id is None:
        # For super admins, allow listing all
        if not current_user.get("is_super_admin"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="customerId is required"
            )
    else:
        await _ensure_subscription_access(current_user, customer_id)
    
    subscriptions = await subscriptions_repo.list_subscriptions(
        customer_id=customer_id,
        status=status,
        category_id=category_id,
        end_before=end_before,
        end_after=end_after,
        limit=limit,
    )
    
    # Convert Decimal to string for JSON serialization
    result: list[dict[str, Any]] = []
    for sub in subscriptions:
        sub_dict = dict(sub)
        sub_dict["unit_price"] = str(sub["unit_price"])
        sub_dict["prorated_price"] = (
            str(sub["prorated_price"]) if sub["prorated_price"] is not None else None
        )
        sub_dict["created_at"] = (
            sub["created_at"].isoformat() if sub.get("created_at") else None
        )
        sub_dict["updated_at"] = (
            sub["updated_at"].isoformat() if sub.get("updated_at") else None
        )
        result.append(sub_dict)
    
    return [SubscriptionResponse.model_validate(sub) for sub in result]


@router.get("/{subscription_id}", response_model=SubscriptionResponse)
async def get_subscription(
    subscription_id: str,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> SubscriptionResponse:
    """Get details of a specific subscription."""
    subscription = await subscriptions_repo.get_subscription(subscription_id)
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )
    
    # Check access
    await _ensure_subscription_access(current_user, subscription["customer_id"])
    
    # Convert Decimal to string for JSON
    sub_dict = dict(subscription)
    sub_dict["unit_price"] = str(subscription["unit_price"])
    sub_dict["prorated_price"] = (
        str(subscription["prorated_price"]) 
        if subscription["prorated_price"] is not None 
        else None
    )
    sub_dict["created_at"] = (
        subscription["created_at"].isoformat() if subscription.get("created_at") else None
    )
    sub_dict["updated_at"] = (
        subscription["updated_at"].isoformat() if subscription.get("updated_at") else None
    )
    
    return SubscriptionResponse.model_validate(sub_dict)
