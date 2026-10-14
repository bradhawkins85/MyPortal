"""JSON API for the Custom Forms feature.

Admin CRUD lives under ``/api/forms``; the public submission endpoint is at
``/forms/{slug}/submit``. All admin endpoints require a super admin.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import JSONResponse

from app.api.dependencies.auth import get_current_user
from app.repositories import forms as forms_repo
from app.schemas.forms import (
    FieldReorder,
    FormAccessUpdate,
    FormCreate,
    FormFieldCreate,
    FormFieldUpdate,
    FormUpdate,
)
from app.services import custom_forms
from app.services.custom_forms import (
    CONFIG_TYPES,
    FILE_TYPES,
    FormValidationError,
    OPTION_TYPES,
)

router = APIRouter(tags=["forms"])


def _require_admin(user: dict[str, Any]) -> dict[str, Any]:
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin access is required.")
    return user


async def _get_admin_form(form_id: int, user: dict[str, Any]) -> dict[str, Any]:
    _require_admin(user)
    form = await forms_repo.get_form(form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found.")
    return form


async def _get_admin_field(
    form_id: int, field_id: int, user: dict[str, Any]
) -> dict[str, Any]:
    await _get_admin_form(form_id, user)
    field = await forms_repo.get_field(field_id)
    if not field or field["form_id"] != form_id:
        raise HTTPException(status_code=404, detail="Field not found.")
    return field


# ---------------------------------------------------------------------------
# Admin: forms
# ---------------------------------------------------------------------------


@router.get("/api/forms")
async def api_list_forms(user: dict[str, Any] = Depends(get_current_user)):
    _require_admin(user)
    return await forms_repo.list_forms()


@router.post("/api/forms")
async def api_create_form(
    payload: FormCreate, user: dict[str, Any] = Depends(get_current_user)
):
    _require_admin(user)
    existing = [form["slug"] for form in await forms_repo.list_forms()]
    slug = custom_forms.unique_slug(payload.name, existing)
    form = await forms_repo.create_form(
        slug=slug,
        name=payload.name.strip(),
        description=payload.description,
        created_by=user.get("id"),
    )
    return form


@router.get("/api/forms/{form_id}")
async def api_get_form(
    form_id: int, user: dict[str, Any] = Depends(get_current_user)
):
    form = await _get_admin_form(form_id, user)
    fields = await forms_repo.list_fields(form_id)
    return custom_forms.serialize_form(form, fields)


@router.patch("/api/forms/{form_id}")
async def api_update_form(
    form_id: int,
    payload: FormUpdate,
    user: dict[str, Any] = Depends(get_current_user),
):
    form = await _get_admin_form(form_id, user)
    name = (payload.name if payload.name is not None else form["name"]).strip()
    description = (
        payload.description
        if payload.description is not None
        else form.get("description")
    )
    active = payload.active if payload.active is not None else form.get("active", True)
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    return await forms_repo.update_form(
        form_id, name=name, description=description, active=active
    )


@router.delete("/api/forms/{form_id}")
async def api_delete_form(
    form_id: int, user: dict[str, Any] = Depends(get_current_user)
):
    await _get_admin_form(form_id, user)
    await forms_repo.delete_form(form_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin: fields
# ---------------------------------------------------------------------------


@router.post("/api/forms/{form_id}/fields")
async def api_create_field(
    form_id: int,
    payload: FormFieldCreate,
    user: dict[str, Any] = Depends(get_current_user),
):
    await _get_admin_form(form_id, user)
    errors = custom_forms.validate_field_definition(
        payload.field_type, payload.label, payload.options, payload.config
    )
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    options = payload.options if payload.field_type in OPTION_TYPES else None
    config = payload.config if payload.field_type in CONFIG_TYPES else None
    sort_order = len(await forms_repo.list_fields(form_id))
    return await forms_repo.create_field(
        form_id=form_id,
        field_type=payload.field_type,
        label=payload.label.strip(),
        placeholder=payload.placeholder,
        help_text=payload.help_text,
        required=payload.required,
        options=options,
        default_value=payload.default_value,
        config=config,
        sort_order=sort_order,
    )


@router.patch("/api/forms/{form_id}/fields/{field_id}")
async def api_update_field(
    form_id: int,
    field_id: int,
    payload: FormFieldUpdate,
    user: dict[str, Any] = Depends(get_current_user),
):
    existing = await _get_admin_field(form_id, field_id, user)
    new_type = payload.field_type or existing["field_type"]
    new_label = (
        payload.label if payload.label is not None else existing["label"]
    ).strip()
    new_placeholder = (
        payload.placeholder
        if payload.placeholder is not None
        else existing["placeholder"]
    )
    new_help = (
        payload.help_text if payload.help_text is not None else existing["help_text"]
    )
    new_required = (
        payload.required
        if payload.required is not None
        else existing["required"]
    )
    new_default = (
        payload.default_value
        if payload.default_value is not None
        else existing["default_value"]
    )
    new_options = (
        payload.options if payload.options is not None else existing["options"]
    )
    if new_type not in OPTION_TYPES:
        new_options = None
    new_config = (
        payload.config if payload.config is not None else existing.get("config")
    )
    if new_type not in CONFIG_TYPES:
        new_config = None
    errors = custom_forms.validate_field_definition(
        new_type, new_label, new_options, new_config
    )
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    return await forms_repo.update_field(
        field_id,
        field_type=new_type,
        label=new_label,
        placeholder=new_placeholder,
        help_text=new_help,
        required=new_required,
        options=new_options,
        default_value=new_default,
        config=new_config,
    )


@router.delete("/api/forms/{form_id}/fields/{field_id}")
async def api_delete_field(
    form_id: int,
    field_id: int,
    user: dict[str, Any] = Depends(get_current_user),
):
    await _get_admin_field(form_id, field_id, user)
    await forms_repo.delete_field(field_id)
    return {"ok": True}


@router.put("/api/forms/{form_id}/fields/reorder")
async def api_reorder_fields(
    form_id: int,
    payload: FieldReorder,
    user: dict[str, Any] = Depends(get_current_user),
):
    await _get_admin_form(form_id, user)
    existing_ids = {f["id"] for f in await forms_repo.list_fields(form_id)}
    if set(payload.field_ids) != existing_ids:
        raise HTTPException(status_code=400, detail="Reorder must include every field.")
    await forms_repo.reorder_fields(form_id, [int(fid) for fid in payload.field_ids])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin: access
# ---------------------------------------------------------------------------


@router.get("/api/forms/{form_id}/access")
async def api_get_access(
    form_id: int, user: dict[str, Any] = Depends(get_current_user)
):
    await _get_admin_form(form_id, user)
    return {"user_ids": await forms_repo.list_access_user_ids(form_id)}


@router.put("/api/forms/{form_id}/access")
async def api_set_access(
    form_id: int,
    payload: FormAccessUpdate,
    user: dict[str, Any] = Depends(get_current_user),
):
    await _get_admin_form(form_id, user)
    current = set(await forms_repo.list_access_user_ids(form_id))
    desired = {int(uid) for uid in payload.user_ids}
    for uid in current - desired:
        await forms_repo.revoke_access(form_id, uid)
    for uid in desired - current:
        await forms_repo.grant_access(form_id, uid)
    return {"user_ids": sorted(desired)}


# ---------------------------------------------------------------------------
# Admin: submissions
# ---------------------------------------------------------------------------


@router.get("/api/forms/{form_id}/submissions")
async def api_list_submissions(
    form_id: int, user: dict[str, Any] = Depends(get_current_user)
):
    await _get_admin_form(form_id, user)
    submissions = await forms_repo.list_submissions(form_id)
    for submission in submissions:
        if isinstance(submission.get("data"), str):
            try:
                submission["data"] = json.loads(submission["data"])
            except (TypeError, ValueError):
                submission["data"] = None
    return submissions


# ---------------------------------------------------------------------------
# Public: submit
# ---------------------------------------------------------------------------


@router.post("/forms/{slug}/submit")
async def api_submit_form(
    slug: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
):
    form = await forms_repo.get_form_by_slug(slug)
    if not form or not form.get("active"):
        raise HTTPException(status_code=404, detail="Form not found.")
    user_id = int(user["id"])
    if not await forms_repo.can_access_form(
        form["id"], user_id, is_super_admin=bool(user.get("is_super_admin"))
    ):
        raise HTTPException(
            status_code=403, detail="You do not have access to this form."
        )
    fields = await forms_repo.list_fields(form["id"])

    parsed = await request.form()
    values: dict[str, Any] = {}
    raw_data = parsed.get("form_data")
    if isinstance(raw_data, str) and raw_data:
        try:
            parsed_json = json.loads(raw_data)
        except ValueError:
            raise HTTPException(status_code=400, detail="form_data must be valid JSON.")
        if isinstance(parsed_json, dict):
            for key, value in parsed_json.items():
                try:
                    values[int(key)] = value
                except (TypeError, ValueError):
                    values[key] = value

    files_by_field: dict[int, list[Any]] = {}
    for field in fields:
        if field["field_type"] in FILE_TYPES:
            files_by_field[field["id"]] = parsed.getlist(f"file_{field['id']}")

    try:
        result = await custom_forms.submit_form(
            form=form,
            fields=fields,
            values=values,
            files=files_by_field,
            requester=user,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except FormValidationError as exc:
        raise HTTPException(status_code=400, detail={"errors": exc.errors})

    return JSONResponse(
        status_code=201,
        content={
            "status": "ok",
            "message": "Your submission was received.",
            "ticket_id": result["ticket_id"],
            "submission_id": result["submission_id"],
            "attachments": result["attachments"],
        },
    )