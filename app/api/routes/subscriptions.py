"""API routes for managing subscriptions."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.repositories import subscription_change_requests as change_requests_repo
from app.repositories import subscriptions as subscriptions_repo
from app.repositories import user_companies as user_company_repo
from app.services import subscription_changes as subscription_changes_service

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


class UpdateSubscriptionRequest(BaseModel):
    """Request model for updating a subscription (super admin only)."""
    
    status: str | None = None
    auto_renew: bool | None = Field(None, alias="autoRenew")
    
    class Config:
        populate_by_name = True


@router.patch("/{subscription_id}", response_model=SubscriptionResponse)
async def update_subscription(
    subscription_id: str,
    update_data: UpdateSubscriptionRequest,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> SubscriptionResponse:
    """Update a subscription (super admin only - can edit status and auto_renew)."""
    # Only super admins can update subscriptions
    if not current_user.get("is_super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin privileges required to update subscriptions"
        )
    
    # Get the subscription to ensure it exists
    subscription = await subscriptions_repo.get_subscription(subscription_id)
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )
    
    # Validate status if provided
    if update_data.status is not None:
        valid_statuses = ["active", "pending_renewal", "expired", "canceled"]
        if update_data.status not in valid_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
            )
    
    # Update the subscription
    await subscriptions_repo.update_subscription(
        subscription_id,
        status=update_data.status,
        auto_renew=update_data.auto_renew,
    )
    
    # Fetch and return the updated subscription
    updated = await subscriptions_repo.get_subscription(subscription_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch updated subscription"
        )
    
    # Convert Decimal to string for JSON
    sub_dict = dict(updated)
    sub_dict["unit_price"] = str(updated["unit_price"])
    sub_dict["prorated_price"] = (
        str(updated["prorated_price"]) 
        if updated["prorated_price"] is not None 
        else None
    )
    sub_dict["created_at"] = (
        updated["created_at"].isoformat() if updated.get("created_at") else None
    )
    sub_dict["updated_at"] = (
        updated["updated_at"].isoformat() if updated.get("updated_at") else None
    )
    
    return SubscriptionResponse.model_validate(sub_dict)


@router.delete("/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(
    subscription_id: str,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> None:
    """Delete a subscription (super admin only)."""
    # Only super admins can delete subscriptions
    if not current_user.get("is_super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin privileges required to delete subscriptions"
        )
    
    # Get the subscription to ensure it exists
    subscription = await subscriptions_repo.get_subscription(subscription_id)
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )
    
    # Delete the subscription
    await subscriptions_repo.delete_subscription(subscription_id)


class ChangePreviewRequest(BaseModel):
    """Request model for previewing a subscription change."""
    
    quantity_change: int = Field(..., alias="quantityChange", gt=0)
    change_type: str = Field(..., alias="changeType", pattern="^(addition|decrease)$")
    
    class Config:
        populate_by_name = True


class ChangePreviewResponse(BaseModel):
    """Response model for subscription change preview."""
    
    current_quantity: int = Field(..., alias="currentQuantity")
    requested_change: int = Field(..., alias="requestedChange")
    change_type: str = Field(..., alias="changeType")
    new_net_additions: int = Field(..., alias="newNetAdditions")
    new_net_decreases: int = Field(..., alias="newNetDecreases")
    new_net_change: int = Field(..., alias="newNetChange")
    new_quantity_at_term_end: int = Field(..., alias="newQuantityAtTermEnd")
    new_total_charges: str = Field(..., alias="newTotalCharges")  # Decimal as string
    prorated_charge: str | None = Field(None, alias="proratedCharge")
    prorated_explanation: dict[str, Any] | None = Field(None, alias="proratedExplanation")
    end_date: str = Field(..., alias="endDate")
    pending_changes_count: int = Field(..., alias="pendingChangesCount")
    
    class Config:
        populate_by_name = True


@router.post("/{subscription_id}/preview-change", response_model=ChangePreviewResponse)
async def preview_change(
    subscription_id: str,
    preview_request: ChangePreviewRequest,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> ChangePreviewResponse:
    """Preview the impact of a subscription change.
    
    Shows prorata charges for additions and net impact with pending changes.
    
    Requires:
    - User must be company admin
    - User must have both License and Cart permissions
    """
    # Get subscription and check access
    subscription = await subscriptions_repo.get_subscription(subscription_id)
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )
    
    await _ensure_subscription_access(current_user, subscription["customer_id"])
    
    try:
        preview = await subscription_changes_service.preview_subscription_change(
            subscription_id,
            preview_request.quantity_change,
            preview_request.change_type,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    # Format prorated explanation if present
    prorated_explanation = None
    if preview.get("prorated_explanation"):
        exp = preview["prorated_explanation"]
        prorated_explanation = {
            "perLicenseCharge": str(exp["per_license_charge"]),
            "quantity": exp["quantity"],
            "totalCharge": str(exp["total_charge"]),
            "daysRemaining": exp["days_remaining"],
            "unitPrice": str(exp["unit_price"]),
            "endDate": exp["end_date"].isoformat(),
            "formula": exp["formula"],
        }
    
    return ChangePreviewResponse(
        current_quantity=preview["current_quantity"],
        requested_change=preview["requested_change"],
        change_type=preview["change_type"],
        new_net_additions=preview["new_net_additions"],
        new_net_decreases=preview["new_net_decreases"],
        new_net_change=preview["new_net_change"],
        new_quantity_at_term_end=preview["new_quantity_at_term_end"],
        new_total_charges=str(preview["new_total_charges"]),
        prorated_charge=(
            str(preview["prorated_charge"]) if preview.get("prorated_charge") else None
        ),
        prorated_explanation=prorated_explanation,
        end_date=preview["end_date"].isoformat(),
        pending_changes_count=len(preview["pending_changes"]),
    )


class ApplyAdditionRequest(BaseModel):
    """Request model for applying a license addition."""
    
    quantity: int = Field(..., gt=0)
    notes: str | None = None
    
    class Config:
        populate_by_name = True


class ApplyAdditionResponse(BaseModel):
    """Response model for applying a license addition."""
    
    success: bool
    message: str
    prorated_charge: str = Field(..., alias="proratedCharge")
    new_quantity: int = Field(..., alias="newQuantity")
    change_request_id: str = Field(..., alias="changeRequestId")
    
    class Config:
        populate_by_name = True


@router.post("/{subscription_id}/add-licenses", response_model=ApplyAdditionResponse)
async def add_licenses(
    subscription_id: str,
    addition_request: ApplyAdditionRequest,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> ApplyAdditionResponse:
    """Add licenses to a subscription immediately with prorata charge.
    
    Requires:
    - User must be company admin
    - User must have both License and Cart permissions
    """
    # Get subscription and check access
    subscription = await subscriptions_repo.get_subscription(subscription_id)
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )
    
    await _ensure_subscription_access(current_user, subscription["customer_id"])
    
    try:
        result = await subscription_changes_service.apply_subscription_addition(
            subscription_id,
            addition_request.quantity,
            current_user["id"],
            addition_request.notes,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    return ApplyAdditionResponse(
        success=True,
        message=f"Successfully added {addition_request.quantity} license(s)",
        prorated_charge=str(result["prorated_charge"]),
        new_quantity=result["subscription"]["quantity"],
        change_request_id=result["change_request"]["id"],
    )


class RequestDecreaseRequest(BaseModel):
    """Request model for requesting a license decrease."""
    
    quantity: int = Field(..., gt=0)
    notes: str | None = None
    
    class Config:
        populate_by_name = True


class RequestDecreaseResponse(BaseModel):
    """Response model for requesting a license decrease."""
    
    success: bool
    message: str
    change_request_id: str = Field(..., alias="changeRequestId")
    quantity_at_term_end: int = Field(..., alias="quantityAtTermEnd")
    end_date: str = Field(..., alias="endDate")
    
    class Config:
        populate_by_name = True


@router.post("/{subscription_id}/request-decrease", response_model=RequestDecreaseResponse)
async def request_decrease(
    subscription_id: str,
    decrease_request: RequestDecreaseRequest,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> RequestDecreaseResponse:
    """Request a license decrease to be applied at term end.
    
    Requires:
    - User must be company admin
    - User must have both License and Cart permissions
    """
    # Get subscription and check access
    subscription = await subscriptions_repo.get_subscription(subscription_id)
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )
    
    await _ensure_subscription_access(current_user, subscription["customer_id"])
    
    try:
        result = await subscription_changes_service.request_subscription_decrease(
            subscription_id,
            decrease_request.quantity,
            current_user["id"],
            decrease_request.notes,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    preview = result["preview"]
    
    return RequestDecreaseResponse(
        success=True,
        message=f"Requested decrease of {decrease_request.quantity} license(s) at term end",
        change_request_id=result["change_request"]["id"],
        quantity_at_term_end=preview["new_quantity_at_term_end"],
        end_date=preview["end_date"].isoformat(),
    )


class PendingChangeResponse(BaseModel):
    """Response model for a pending change."""
    
    id: str
    change_type: str = Field(..., alias="changeType")
    quantity_change: int = Field(..., alias="quantityChange")
    requested_at: str = Field(..., alias="requestedAt")
    prorated_charge: str | None = Field(None, alias="proratedCharge")
    notes: str | None = None
    
    class Config:
        populate_by_name = True


@router.get("/{subscription_id}/pending-changes", response_model=list[PendingChangeResponse])
async def get_pending_changes(
    subscription_id: str,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> list[PendingChangeResponse]:
    """Get all pending change requests for a subscription.
    
    Requires:
    - User must be company admin
    - User must have both License and Cart permissions
    """
    # Get subscription and check access
    subscription = await subscriptions_repo.get_subscription(subscription_id)
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )
    
    await _ensure_subscription_access(current_user, subscription["customer_id"])
    
    pending_changes = await change_requests_repo.list_pending_changes_for_subscription(
        subscription_id
    )
    
    return [
        PendingChangeResponse(
            id=change["id"],
            change_type=change["change_type"],
            quantity_change=change["quantity_change"],
            requested_at=change["requested_at"].isoformat(),
            prorated_charge=(
                str(change["prorated_charge"]) if change.get("prorated_charge") else None
            ),
            notes=change.get("notes"),
        )
        for change in pending_changes
    ]


@router.delete("/change-requests/{change_request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_change_request(
    change_request_id: str,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
) -> None:
    """Cancel a pending change request.
    
    Can only cancel pending decrease requests (additions are applied immediately).
    
    Requires:
    - User must be company admin
    - User must have both License and Cart permissions
    """
    # Get the change request
    change_request = await change_requests_repo.get_change_request(change_request_id)
    if not change_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change request not found"
        )
    
    # Get the subscription and check access
    subscription = await subscriptions_repo.get_subscription(change_request["subscription_id"])
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )
    
    await _ensure_subscription_access(current_user, subscription["customer_id"])
    
    try:
        await subscription_changes_service.cancel_pending_change(change_request_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
