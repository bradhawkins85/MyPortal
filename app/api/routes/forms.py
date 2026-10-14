"""JSON API for the Custom Forms feature.

Admin CRUD lives under ``/api/forms``; the public submission endpoint is at
``/forms/{slug}/submit``. All admin endpoints require a super admin.
"""
from __future__ import annotations

import json
import base64
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
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
from app.services.custom_forms import CONFIG_TYPES, FILE_TYPES, FormValidationError, OPTION_TYPES
from app.security.flash import flash_redirect


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
        if "description" in payload.model_fields_set
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
        if "placeholder" in payload.model_fields_set
        else existing["placeholder"]
    )
    new_help = (
        payload.help_text if "help_text" in payload.model_fields_set else existing["help_text"]
    )
    new_required = (
        payload.required
        if payload.required is not None
        else existing["required"]
    )
    new_default = (
        payload.default_value
        if "default_value" in payload.model_fields_set
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
# HTML admin form submissions
# ---------------------------------------------------------------------------


@router.post("/admin/custom-forms/save", include_in_schema=False)
async def save_custom_form_page(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
):
    """Progressive enhancement fallback, using the same validation as the API."""
    _require_admin(user)
    parsed = await request.form()
    destination = "/admin/custom-forms"
    try:
        action = str(parsed.get("action") or "")
        if action == "create_form":
            form = await api_create_form(
                payload=FormCreate(name=str(parsed.get("name") or "").strip(), description=parsed.get("description") or None),
                user=user,
            )
            return flash_redirect(f"{destination}?form_id={form['id']}", "Form created. Add your first field.", "success")
        form_id = int(parsed.get("form_id") or 0)
        await _get_admin_form(form_id, user)
        destination += f"?form_id={form_id}"
        if action == "update_form":
            await api_update_form(form_id, FormUpdate(
                name=str(parsed.get("name") or "").strip(), description=parsed.get("description") or None,
                active=parsed.get("active") == "1",
            ), user)
        elif action == "delete_form":
            await api_delete_form(form_id, user)
            destination = "/admin/custom-forms"
        elif action == "save_field":
            field_type = str(parsed.get("field_type") or "")
            config = None
            if field_type == "matrix":
                config = {
                    "rows": [line.strip() for line in str(parsed.get("matrix_rows") or "").splitlines() if line.strip()],
                    "columns": [line.strip() for line in str(parsed.get("matrix_columns") or "").splitlines() if line.strip()],
                }
            elif field_type == "rating":
                config = {"max": int(parsed.get("rating_max") or 5)}
            elif field_type == "audio":
                config = {"max_clips": int(parsed.get("audio_max") or 5)}
            payload = {
                "field_type": field_type,
                "config": config,
                "label": str(parsed.get("label") or "").strip(),
                "placeholder": parsed.get("placeholder") or None,
                "help_text": parsed.get("help_text") or None,
                "default_value": parsed.get("default_value") or None,
                "required": parsed.get("required") == "1",
                "options": [line.strip() for line in str(parsed.get("options") or "").splitlines() if line.strip()],
            }
            if parsed.get("field_id"):
                await api_update_field(form_id, int(parsed["field_id"]), FormFieldUpdate(**payload), user)
            else:
                await api_create_field(form_id, FormFieldCreate(**payload), user)
        elif action == "move_field":
            order = [field["id"] for field in await forms_repo.list_fields(form_id)]
            field_id = int(parsed.get("field_id") or 0)
            if field_id not in order or parsed.get("direction") not in {"up", "down"}:
                raise HTTPException(status_code=400, detail="Choose a valid field and direction.")
            index = order.index(field_id)
            other = index + (-1 if parsed["direction"] == "up" else 1)
            if 0 <= other < len(order):
                order[index], order[other] = order[other], order[index]
                await api_reorder_fields(form_id, FieldReorder(field_ids=order), user)
        elif action == "delete_field":
            await api_delete_field(form_id, int(parsed.get("field_id") or 0), user)
        elif action in {"add_access", "remove_access"}:
            user_id = int(parsed.get("user_id") or 0)
            if user_id < 1:
                raise HTTPException(status_code=400, detail="Choose a user to update access.")
            ids = set(await forms_repo.list_access_user_ids(form_id))
            if action == "add_access":
                ids.add(user_id)
            else:
                ids.discard(user_id)
            await api_set_access(form_id, FormAccessUpdate(user_ids=sorted(ids)), user)
        else:
            raise HTTPException(status_code=400, detail="Unknown form action.")
    except ValidationError as exc:
        message = "; ".join(f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors())
        return flash_redirect(destination, message, "error")
    except ValueError:
        return flash_redirect(destination, "Choose a valid form, field or user.", "error")
    except HTTPException as exc:
        if exc.status_code in {401, 403, 404}:
            raise
        return flash_redirect(destination, str(exc.detail), "error")
    return flash_redirect(destination, "Changes saved.", "success")


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
    html_submission = "text/html" in request.headers.get("accept", "") and not request.headers.get("x-requested-with")
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
    else:
        for field in fields:
            key = f"field_{field['id']}"
            values[field["id"]] = parsed.getlist(key) if field["field_type"] == "multiselect" else parsed.get(key, "")
            if field["field_type"] == "matrix":
                values[field["id"]] = {
                    row: parsed.get(f"matrix_{field['id']}_{index}")
                    for index, row in enumerate((field.get("config") or {}).get("rows", []))
                    if parsed.get(f"matrix_{field['id']}_{index}")
                }
    for field in fields:
        if field["field_type"] == "signature":
            upload = parsed.get(f"signature_{field['id']}")
            if getattr(upload, "filename", None):
                if upload.content_type not in {"image/png", "image/jpeg"}:
                    raise HTTPException(status_code=400, detail="Upload a PNG or JPEG signature image.")
                image_data = await upload.read(1024 * 1024 + 1)
                if len(image_data) > 1024 * 1024:
                    raise HTTPException(status_code=400, detail="Signature images must be 1 MB or smaller.")
                values[field["id"]] = f"data:{upload.content_type};base64," + base64.b64encode(image_data).decode("ascii")

    files_by_field: dict[int, list[Any]] = {}
    for field in fields:
        if field["field_type"] in FILE_TYPES:
            files_by_field[field["id"]] = [
                upload for upload in parsed.getlist(f"file_{field['id']}")
                if getattr(upload, "filename", None)
            ]

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
        if html_submission:
            from app.main import _render_template

            response = await _render_template(
                "forms/submit.html", request, user,
                extra={"title": form["name"], "form": form, "fields": fields, "errors": exc.errors, "submitted_values": values},
            )
            response.status_code = 400
            return response
        raise HTTPException(status_code=400, detail={"errors": exc.errors})

    if html_submission:
        return flash_redirect(f"/tickets/{result['ticket_id']}", "Your request has been submitted.", "success")

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
