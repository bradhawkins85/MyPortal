from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import users as user_repo
from app.schemas.users import UserCreate, UserResponse, UserUpdate

router = APIRouter(prefix="/users", tags=["Users"])


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
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
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
    return updated


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    user = await user_repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await user_repo.delete_user(user_id)
    return None
