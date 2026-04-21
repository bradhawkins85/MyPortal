from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import company_memberships as membership_repo
from app.repositories import roles as role_repo
from app.repositories import sidebar_preferences as sidebar_preferences_repo
from app.repositories import users as user_repo
from app.schemas.users import UserCreate, UserResponse, UserUpdate
from app.services import audit as audit_service

router = APIRouter(prefix="/api/users", tags=["Users"])


# Fields that we never want to leak into audit_logs even via "after" snapshots.
_USER_SENSITIVE_FIELDS: tuple[str, ...] = (
    "password",
    "password_hash",
    "totp_secret",
)


def _audit_user_view(user: dict | None) -> dict | None:
    """Return a copy of a user record with sensitive fields stripped."""

    if not user:
        return None
    return {key: value for key, value in user.items() if key not in _USER_SENSITIVE_FIELDS}


@router.get("/me/sidebar-preferences", response_model=dict[str, list[str]])
async def get_my_sidebar_preferences(
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    return await sidebar_preferences_repo.get_user_sidebar_preferences(int(current_user["id"]))


@router.put("/me/sidebar-preferences", response_model=dict[str, list[str]])
async def update_my_sidebar_preferences(
    payload: dict,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    return await sidebar_preferences_repo.upsert_user_sidebar_preferences(
        int(current_user["id"]),
        payload,
    )


@router.get("", response_model=list[UserResponse])
async def list_users(
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    rows = await user_repo.list_users()
    return rows


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    existing = await user_repo.get_user_by_email(payload.email)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    created = await user_repo.create_user(
        email=payload.email,
        password=payload.password,
        first_name=payload.first_name,
        last_name=payload.last_name,
        mobile_phone=payload.mobile_phone,
        company_id=payload.company_id,
    )
    if payload.company_id:
        try:
            existing = await membership_repo.get_membership_by_company_user(
                payload.company_id, created["id"]
            )
            if not existing:
                default_role = await role_repo.get_role_by_name("Member")
                if default_role:
                    await membership_repo.create_membership(
                        company_id=payload.company_id,
                        user_id=created["id"],
                        role_id=default_role["id"],
                        status="active",
                    )
        except Exception:
            # Membership creation is best-effort to avoid blocking user provisioning
            pass
    await audit_service.record(
        action="user.create",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="user",
        entity_id=int(created["id"]) if created.get("id") is not None else None,
        before=None,
        after=_audit_user_view(created),
        metadata={"company_id": payload.company_id} if payload.company_id else None,
    )
    return created


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    if current_user["id"] != user_id and not current_user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    user = await user_repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    if current_user["id"] != user_id and not current_user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    user = await user_repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    data = payload.model_dump(exclude_unset=True)
    if "is_super_admin" in data and not current_user.get("is_super_admin"):
        data.pop("is_super_admin")
    updated = await user_repo.update_user(user_id, **data)
    metadata: dict[str, object] | None = None
    if "is_super_admin" in data and bool(user.get("is_super_admin")) != bool(data["is_super_admin"]):
        metadata = {"role_changed": True}
    await audit_service.record(
        action="user.update",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="user",
        entity_id=user_id,
        before=_audit_user_view(user),
        after=_audit_user_view(updated),
        metadata=metadata,
    )
    return updated


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    user = await user_repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await user_repo.delete_user(user_id)
    await audit_service.record(
        action="user.delete",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="user",
        entity_id=user_id,
        before=_audit_user_view(user),
        after=None,
    )
    return None
