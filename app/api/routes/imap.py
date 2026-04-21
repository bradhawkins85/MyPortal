from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.core.errors import build_client_http_error, log_exception_with_error_id, new_error_id
from app.schemas.imap import (
    IMAPAccountCreate,
    IMAPAccountResponse,
    IMAPAccountUpdate,
    IMAPSyncResponse,
)
from app.services import audit as audit_service
from app.services import imap as imap_service

router = APIRouter(prefix="/imap", tags=["IMAP"])


# Audit IMAP account snapshots intentionally exclude any field that could
# carry the mailbox password. The diff helper masks fields whose name matches
# 'password', but we also feed it through this whitelist so a future schema
# change cannot accidentally leak credentials.
_IMAP_AUDIT_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "email_address",
    "host",
    "port",
    "use_ssl",
    "use_starttls",
    "username",
    "is_active",
    "company_id",
    "default_assignee_id",
    "default_status",
    "updated_at",
)


def _audit_imap_view(account: dict | None) -> dict | None:
    if not account:
        return None
    return {key: account.get(key) for key in _IMAP_AUDIT_FIELDS}


@router.get("/accounts", response_model=list[IMAPAccountResponse])
async def list_accounts(
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> list[IMAPAccountResponse]:
    accounts = await imap_service.list_accounts()
    return [IMAPAccountResponse.model_validate(account) for account in accounts]


@router.post("/accounts", response_model=IMAPAccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: IMAPAccountCreate,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
) -> IMAPAccountResponse:
    data = payload.model_dump()
    data["password"] = payload.password.get_secret_value()
    try:
        account = await imap_service.create_account(data)
    except ValueError as exc:
        error_id = new_error_id()
        log_exception_with_error_id("Failed to create IMAP account", error_id=error_id, route="imap.create_account")
        raise build_client_http_error(
            status.HTTP_400_BAD_REQUEST,
            "Unable to create IMAP account. Please verify the account details and try again.",
            error_id=error_id,
        ) from exc
    await audit_service.record(
        action="imap.account.create",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="imap_account",
        entity_id=int(account["id"]) if account.get("id") is not None else None,
        before=None,
        after=_audit_imap_view(account),
        sensitive_extra_keys=("password",),
    )
    return IMAPAccountResponse.model_validate(account)


@router.get("/accounts/{account_id}", response_model=IMAPAccountResponse)
async def get_account(
    account_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> IMAPAccountResponse:
    account = await imap_service.get_account(account_id)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return IMAPAccountResponse.model_validate(account)


@router.put("/accounts/{account_id}", response_model=IMAPAccountResponse)
async def update_account(
    account_id: int,
    payload: IMAPAccountUpdate,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
) -> IMAPAccountResponse:
    existing = await imap_service.get_account(account_id)
    data = payload.model_dump(exclude_unset=True)
    if "password" in data and data["password"] is not None:
        if payload.password is None:
            data.pop("password", None)
        else:
            data["password"] = payload.password.get_secret_value()
    try:
        account = await imap_service.update_account(account_id, data)
    except ValueError as exc:
        error_id = new_error_id()
        log_exception_with_error_id(
            "Failed to update IMAP account",
            error_id=error_id,
            route="imap.update_account",
            account_id=account_id,
        )
        raise build_client_http_error(
            status.HTTP_400_BAD_REQUEST,
            "Unable to update IMAP account. Please verify the account details and try again.",
            error_id=error_id,
        ) from exc
    await audit_service.record(
        action="imap.account.update",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="imap_account",
        entity_id=account_id,
        before=_audit_imap_view(existing),
        after=_audit_imap_view(account),
        metadata={"password_changed": True} if "password" in data else None,
        sensitive_extra_keys=("password",),
    )
    return IMAPAccountResponse.model_validate(account)


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
) -> None:
    existing = await imap_service.get_account(account_id)
    await imap_service.delete_account(account_id)
    await audit_service.record(
        action="imap.account.delete",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="imap_account",
        entity_id=account_id,
        before=_audit_imap_view(existing),
        after=None,
        sensitive_extra_keys=("password",),
    )
    return None


@router.post("/accounts/{account_id}/sync", response_model=IMAPSyncResponse)
async def sync_account(
    account_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> IMAPSyncResponse:
    result = await imap_service.sync_account(account_id)
    return IMAPSyncResponse.model_validate(result)


@router.post(
    "/accounts/{account_id}/clone",
    response_model=IMAPAccountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def clone_account(
    account_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> IMAPAccountResponse:
    try:
        account = await imap_service.clone_account(account_id)
    except LookupError as exc:
        error_id = new_error_id()
        log_exception_with_error_id(
            "Failed to clone IMAP account",
            error_id=error_id,
            route="imap.clone_account",
            account_id=account_id,
        )
        raise build_client_http_error(
            status.HTTP_404_NOT_FOUND,
            "IMAP account not found.",
            error_id=error_id,
        ) from exc
    except ValueError as exc:
        error_id = new_error_id()
        log_exception_with_error_id(
            "Failed to clone IMAP account",
            error_id=error_id,
            route="imap.clone_account",
            account_id=account_id,
        )
        raise build_client_http_error(
            status.HTTP_400_BAD_REQUEST,
            "Unable to clone IMAP account.",
            error_id=error_id,
        ) from exc
    return IMAPAccountResponse.model_validate(account)
