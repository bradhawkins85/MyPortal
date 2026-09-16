"""Documented API for reviewed Microsoft 365 spam search and purge jobs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies.auth import get_current_user, require_helpdesk_technician
from app.api.dependencies.database import require_database
from app.repositories import m365_spam_purge as purge_repo
from app.schemas.m365_spam_purge import SpamPurgeRequestCreate, SpamPurgeRequestResponse
from app.schemas.m365_out_of_office import OutOfOfficeCreate, OutOfOfficeResult
from app.security.session import session_manager
from app.services import m365_spam_purge as purge_service
from app.services import m365_out_of_office as oof_service
from app.repositories import m365 as m365_repo


router = APIRouter(
    prefix="/m365/spam-purge/api",
    tags=["Office365 Spam Purge"],
    dependencies=[Depends(require_database)],
)
oof_router = APIRouter(
    prefix="/m365/out-of-office/api",
    tags=["Office365 Out of Office"],
    dependencies=[Depends(require_database)],
)


async def _company_id(request: Request) -> int:
    session = await session_manager.load_session(request)
    if not session or session.active_company_id is None:
        raise HTTPException(status_code=400, detail="Select a company before using spam purge")
    return int(session.active_company_id)


async def _require_oof_access(request: Request, user: dict, *, write: bool = False) -> None:
    from app import main as main_module
    if not await main_module._has_menu_page_access(
        request, user, "menu.m365.out_of_office", write=write
    ):
        raise HTTPException(status_code=403, detail="Out of Office permission required")


@oof_router.get("/mailboxes", summary="List selectable user mailboxes")
async def list_out_of_office_mailboxes(request: Request, user: dict = Depends(get_current_user)):
    await _require_oof_access(request, user)
    rows = await m365_repo.get_mailboxes(await _company_id(request), "UserMailbox")
    return [{"display_name": row["display_name"], "user_principal_name": row["user_principal_name"]} for row in rows]


@oof_router.post("", response_model=list[OutOfOfficeResult], summary="Schedule automatic replies for user mailboxes")
async def set_out_of_office(payload: OutOfOfficeCreate, request: Request, user: dict = Depends(get_current_user)):
    await _require_oof_access(request, user, write=True)
    try:
        return await oof_service.set_automatic_replies(await _company_id(request), payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/requests", response_model=list[SpamPurgeRequestResponse])
async def list_requests(request: Request, _: dict = Depends(require_helpdesk_technician)):
    return await purge_repo.list_requests(await _company_id(request))


@router.post("/requests", response_model=SpamPurgeRequestResponse, status_code=status.HTTP_201_CREATED)
async def create_request(
    payload: SpamPurgeRequestCreate, request: Request,
    user: dict = Depends(require_helpdesk_technician),
):
    return await purge_service.create_request(
        await _company_id(request), int(user["id"]), payload.model_dump()
    )


@router.get("/requests/{request_id}", response_model=SpamPurgeRequestResponse)
async def get_request(
    request_id: int, request: Request, _: dict = Depends(require_helpdesk_technician),
):
    item = await purge_repo.get_request(request_id)
    if not item or int(item["company_id"]) != await _company_id(request):
        raise HTTPException(status_code=404, detail="Spam purge request not found")
    return item


@router.post("/requests/{request_id}/search", response_model=SpamPurgeRequestResponse)
async def start_search(
    request_id: int, request: Request, _: dict = Depends(require_helpdesk_technician),
):
    try:
        return await purge_service.start_search(request_id, await _company_id(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/requests/{request_id}/purge", response_model=SpamPurgeRequestResponse)
async def start_purge(
    request_id: int, request: Request, _: dict = Depends(require_helpdesk_technician),
):
    try:
        return await purge_service.start_purge(request_id, await _company_id(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/requests/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_request(
    request_id: int, request: Request, _: dict = Depends(require_helpdesk_technician),
):
    if not await purge_repo.delete_request(request_id, await _company_id(request)):
        raise HTTPException(status_code=409, detail="Only unpurged draft or failed requests can be deleted")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
