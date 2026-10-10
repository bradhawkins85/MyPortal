"""Tags on assets and companies.

* ``GET /api/tags`` search tags, ``POST /api/tags`` create one (asset editors)
* ``GET``/``PUT /api/assets/{asset_id}/tags`` an asset's tags (``menu.assets``)
* ``POST``/``DELETE /api/assets/{asset_id}/tags/{tag_id}/block`` keep a tag off
  one asset, or lift that block (``menu.assets``)
* ``GET``/``PUT /api/companies/{company_id}/tags`` a company's tags (super admin)
* ``GET /admin/tags`` manage the tag list; rename, recolour, delete and
  re-apply the automatic tags (super admin)
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.core.logging import log_warning
from app.repositories import assets as asset_repo
from app.repositories import companies as company_repo
from app.repositories import tags as tags_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import tags as tag_rules

router = APIRouter(tags=["Tags"])

MAX_TAGS_PER_RECORD = 50
AUTO_REFRESH_INTERVAL_SECONDS = 6 * 60 * 60
AUTO_REFRESH_STARTUP_DELAY_SECONDS = 60
AUTO_REFRESH_RETRY_SECONDS = 10 * 60


class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class TagAssignment(BaseModel):
    tag_ids: list[int] = Field(default_factory=list, max_length=MAX_TAGS_PER_RECORD)


def _main():
    from app import main as main_module

    return main_module


async def _api_user(request: Request) -> dict[str, Any]:
    user, redirect = await _main()._require_authenticated_user(request)
    if redirect or not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


async def can_edit_tags(request: Request, user: dict[str, Any], membership: Any) -> bool:
    """Tags are one internal list shared by every company, so only technicians
    with asset write access (and super admins) see and change them."""
    main_module = _main()
    if user.get("is_super_admin"):
        return True
    return bool(
        main_module._membership_menu_can(user, membership, "menu.assets", write=True)
        and await main_module._is_helpdesk_technician(user, request)
    )


async def _asset_editor(request: Request) -> tuple[dict[str, Any], int | None]:
    """Return ``(user, active company id)`` for someone who may edit asset tags."""
    main_module = _main()
    user = await _api_user(request)
    company_id = user.get("company_id")
    if user.get("is_super_admin"):
        return user, int(company_id) if company_id is not None else None
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Technician asset edit access required")
    membership = await main_module._get_effective_company_membership(request, user["id"], int(company_id))
    if not await can_edit_tags(request, user, membership):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Technician asset edit access required")
    return user, int(company_id)


async def _super_admin(request: Request) -> dict[str, Any]:
    user = await _api_user(request)
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    return user


async def _editable_asset(request: Request, asset_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    user, company_id = await _asset_editor(request)
    asset = await asset_repo.get_asset_by_id(asset_id)
    # Assets are edited in the active company only, super admins included.
    if not asset or company_id is None or int(asset.get("company_id") or 0) != company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return user, asset


async def picker_context(tags: list[dict[str, Any]], *, endpoint: str, can_edit: bool,
                         blocked: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Values for ``tags/_picker.html``: the record's tags and where to save them.

    ``blocked`` (assets only) lists the tags kept off the record; the picker
    then offers to block automatic tags at ``<endpoint>/<tag_id>/block``.
    """
    return {"tags": tags, "endpoint": endpoint, "can_edit": can_edit, "blocked": blocked}


async def asset_tags_context(asset_id: int, *, can_edit: bool) -> dict[str, Any]:
    return await picker_context(
        await tags_repo.list_asset_tags(asset_id), endpoint=f"/api/assets/{int(asset_id)}/tags", can_edit=can_edit,
        blocked=await tags_repo.list_asset_blocked_tags(asset_id),
    )


async def _asset_tags_payload(asset_id: int) -> dict[str, Any]:
    return {
        "tags": await tags_repo.list_asset_tags(asset_id),
        "blocked": await tags_repo.list_asset_blocked_tags(asset_id),
    }


async def company_tags_context(company_id: int, *, can_edit: bool) -> dict[str, Any]:
    return await picker_context(
        await tags_repo.list_company_tags(company_id), endpoint=f"/api/companies/{int(company_id)}/tags", can_edit=can_edit,
    )


# ---------------------------------------------------------------------------
# Tag list API
# ---------------------------------------------------------------------------


@router.get("/api/tags", summary="Search tags")
async def search_tags(request: Request, q: str | None = Query(default=None, max_length=64),
                      limit: int = Query(default=50, ge=1, le=500)):
    await _asset_editor(request)
    return {"tags": await tags_repo.list_tags(search=q, limit=limit)}


@router.post("/api/tags", summary="Create a tag (or return the existing one with that name)")
async def create_tag(request: Request, payload: TagCreate):
    user, _company_id = await _asset_editor(request)
    try:
        tag, created = await tags_repo.get_or_create_tag(payload.name, created_by=user.get("id"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if created:
        await audit_service.record(
            action="tag.create", request=request, user_id=user.get("id"), entity_type="tag",
            entity_id=tag["id"], after={"name": tag["name"]},
        )
    return {"tag": tag, "created": created}


# ---------------------------------------------------------------------------
# Asset and company assignments
# ---------------------------------------------------------------------------


async def _validated_ids(tag_ids: list[int]) -> list[int]:
    ids = await tags_repo.existing_tag_ids(tag_ids)
    if len(ids) > MAX_TAGS_PER_RECORD:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"At most {MAX_TAGS_PER_RECORD} tags")
    return ids


@router.get("/api/assets/{asset_id}/tags", summary="List an asset's tags")
async def get_asset_tags(request: Request, asset_id: int):
    await _editable_asset(request, asset_id)
    return await _asset_tags_payload(asset_id)


@router.put("/api/assets/{asset_id}/tags", summary="Set an asset's hand-picked tags")
async def put_asset_tags(request: Request, asset_id: int, payload: TagAssignment):
    user, _asset = await _editable_asset(request, asset_id)
    before = [tag["name"] for tag in await tags_repo.list_asset_tags(asset_id) if tag.get("source") == "manual"]
    await tags_repo.set_asset_manual_tags(asset_id, await _validated_ids(payload.tag_ids))
    result = await _asset_tags_payload(asset_id)
    await audit_service.record(
        action="asset.tags.update", request=request, user_id=user.get("id"), entity_type="asset", entity_id=asset_id,
        before={"tags": before},
        after={"tags": [tag["name"] for tag in result["tags"] if tag.get("source") == "manual"]},
    )
    return result


@router.post("/api/assets/{asset_id}/tags/{tag_id}/block", summary="Keep a tag off an asset")
async def block_asset_tag(request: Request, asset_id: int, tag_id: int):
    """Stops the automatic rules (and the asset's company tags) giving this asset the tag,
    for example to keep a desktop that acts as a server out of "Workstation"."""
    user, _asset = await _editable_asset(request, asset_id)
    tag = await tags_repo.get_tag(tag_id)
    if not tag or not await tags_repo.block_asset_tag(asset_id, tag_id, blocked_by=user.get("id")):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    await audit_service.record(
        action="asset.tags.block", request=request, user_id=user.get("id"), entity_type="asset", entity_id=asset_id,
        after={"tag": tag["name"]},
    )
    return await _asset_tags_payload(asset_id)


@router.delete("/api/assets/{asset_id}/tags/{tag_id}/block", summary="Lift a tag block on an asset")
async def unblock_asset_tag(request: Request, asset_id: int, tag_id: int):
    user, _asset = await _editable_asset(request, asset_id)
    tag = await tags_repo.get_tag(tag_id)
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    await tags_repo.unblock_asset_tag(asset_id, tag_id)
    await audit_service.record(
        action="asset.tags.unblock", request=request, user_id=user.get("id"), entity_type="asset",
        entity_id=asset_id, before={"tag": tag["name"]},
    )
    return await _asset_tags_payload(asset_id)


@router.get("/api/companies/{company_id}/tags", summary="List a company's tags")
async def get_company_tags(request: Request, company_id: int):
    await _super_admin(request)
    if not await company_repo.get_company_by_id(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return {"tags": await tags_repo.list_company_tags(company_id)}


@router.put("/api/companies/{company_id}/tags", summary="Set a company's tags")
async def put_company_tags(request: Request, company_id: int, payload: TagAssignment):
    user = await _super_admin(request)
    if not await company_repo.get_company_by_id(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    before = [tag["name"] for tag in await tags_repo.list_company_tags(company_id)]
    await tags_repo.set_company_tags(company_id, await _validated_ids(payload.tag_ids))
    tags = await tags_repo.list_company_tags(company_id)
    await audit_service.record(
        action="company.tags.update", request=request, user_id=user.get("id"), entity_type="company",
        entity_id=company_id, before={"tags": before}, after={"tags": [tag["name"] for tag in tags]},
    )
    return {"tags": tags}


# ---------------------------------------------------------------------------
# Admin page
# ---------------------------------------------------------------------------


@router.get("/admin/tags", response_class=HTMLResponse, summary="Manage asset and company tags")
async def tags_admin_page(request: Request):
    main_module = _main()
    user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    await tags_repo.ensure_auto_tags()
    return await main_module._render_template(
        "admin/tags.html", request, user, extra={
            "title": "Tags",
            "tags": await tags_repo.list_tags(with_counts=True),
            "auto_tags": tag_rules.AUTO_TAGS_BY_KEY,
        },
    )


@router.post("/admin/tags", summary="Create a tag")
async def tags_admin_create(request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        tag, created = await tags_repo.get_or_create_tag(str(form.get("name") or ""), created_by=user.get("id"))
        if created and (form.get("colour") or form.get("description")):
            tag = await tags_repo.update_tag(
                tag["id"], name=tag["name"], colour=str(form.get("colour") or "") or None,
                description=str(form.get("description") or ""),
            ) or tag
    except ValueError as exc:
        return flash_redirect("/admin/tags", str(exc), "error")
    if not created:
        return flash_redirect("/admin/tags", f"The tag {tag['name']} already exists.", "info")
    await audit_service.record(
        action="tag.create", request=request, user_id=user.get("id"), entity_type="tag",
        entity_id=tag["id"], after={"name": tag["name"]},
    )
    return flash_redirect("/admin/tags", f"Created the tag {tag['name']}.", "success")


@router.post("/admin/tags/refresh", summary="Re-apply the automatic tags to every asset")
async def tags_admin_refresh(request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    count = await tags_repo.refresh_all_auto_tags()
    await audit_service.record(
        action="tag.auto.refresh", request=request, user_id=user.get("id"), entity_type="tag",
        metadata={"assets": count},
    )
    return flash_redirect("/admin/tags", f"Re-applied the automatic tags to {count} assets.", "success")


@router.post("/admin/tags/{tag_id}", summary="Rename or recolour a tag")
async def tags_admin_update(request: Request, tag_id: int):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    before = await tags_repo.get_tag(tag_id)
    if not before:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    form = await request.form()
    try:
        after = await tags_repo.update_tag(
            tag_id, name=str(form.get("name") or ""), colour=str(form.get("colour") or "") or None,
            description=str(form.get("description") or ""),
        )
    except ValueError as exc:
        return flash_redirect("/admin/tags", str(exc), "error")
    await audit_service.record(
        action="tag.update", request=request, user_id=user.get("id"), entity_type="tag", entity_id=tag_id,
        before=before, after=after,
    )
    return flash_redirect("/admin/tags", f"Saved the tag {after['name'] if after else ''}.", "success")


@router.post("/admin/tags/{tag_id}/delete", summary="Delete a tag")
async def tags_admin_delete(request: Request, tag_id: int):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    before = await tags_repo.get_tag(tag_id)
    try:
        deleted = await tags_repo.delete_tag(tag_id)
    except ValueError as exc:
        return flash_redirect("/admin/tags", str(exc), "error")
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    await audit_service.record(
        action="tag.delete", request=request, user_id=user.get("id"), entity_type="tag", entity_id=tag_id,
        before=before,
    )
    return flash_redirect("/admin/tags", f"Deleted the tag {before['name'] if before else ''}.", "success")


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------


async def refresh_auto_tags_job() -> None:
    """Tag existing assets once at startup, then catch any missed changes periodically.

    Saves and syncs tag assets as they happen; this pass covers assets that
    existed before tags did and any writer that bypasses the asset repository.
    """
    await asyncio.sleep(AUTO_REFRESH_STARTUP_DELAY_SECONDS)
    while True:
        delay = AUTO_REFRESH_INTERVAL_SECONDS
        try:
            await tags_repo.refresh_all_auto_tags()
        except Exception as exc:  # pragma: no cover - retried shortly
            log_warning("Automatic asset tag refresh failed", error=str(exc))
            delay = AUTO_REFRESH_RETRY_SECONDS
        await asyncio.sleep(delay)
