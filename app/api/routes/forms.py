"""JSON API for the Custom Forms feature.

Admin CRUD lives under ``/api/forms``; the public submission endpoint is at
``/forms/{slug}/submit``. All admin endpoints require a super admin.
"""
from __future__ import annotations

import json
import base64
import hashlib
import secrets
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import qrcode
from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from starlette.responses import JSONResponse

from app.api.dependencies.auth import get_current_user
from app.repositories import forms as forms_repo
from app.repositories import companies as companies_repo
from app.schemas.forms import (
    FieldReorder,
    FormAccessUpdate,
    FormCreate,
    FormShareLinkCreate,
    FormFieldCreate,
    FormFieldUpdate,
    FormUpdate,
)
from app.services import custom_forms
from app.services.custom_forms import CONFIG_TYPES, FILE_TYPES, FormValidationError, OPTION_TYPES
from app.security.encryption import decrypt_secret, encrypt_secret
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
    siblings = await forms_repo.list_fields(form_id)
    conditions, errors = custom_forms.validate_conditions(
        payload.conditions.model_dump() if payload.conditions else None, field_id=None, fields=siblings,
    )
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    options = payload.options if payload.field_type in OPTION_TYPES else None
    config = payload.config if payload.field_type in CONFIG_TYPES else None
    sort_order = len(siblings)
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
        conditions=conditions,
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
    new_conditions = existing.get("conditions")
    if "conditions" in payload.model_fields_set:
        new_conditions, condition_errors = custom_forms.validate_conditions(
            payload.conditions.model_dump() if payload.conditions else None,
            field_id=field_id, fields=await forms_repo.list_fields(form_id),
        )
        errors += condition_errors
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
        conditions=new_conditions,
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
                    "mode": str(parsed.get("matrix_mode") or "row"),
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
    return await _submit_form_request(form, request, user)


async def _submit_form_request(form, request, user, *, public=False, submit_action=None):
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
                config = field.get("config") or {}
                if custom_forms.matrix_mode(config) == "table":
                    answer = custom_forms.matrix_cell_answer(config, parsed.get(f"matrix_{field['id']}"))
                    if answer is None:
                        raise HTTPException(status_code=400, detail=f"Choose a valid answer for '{field.get('label', 'the matrix')}'.")
                    values[field["id"]] = answer
                else:
                    values[field["id"]] = {
                        row: parsed.get(f"matrix_{field['id']}_{index}")
                        for index, row in enumerate(config.get("rows", []))
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
            if public:
                return _render_public_form(request, form, fields, submit_action,
                                           errors=exc.errors, submitted_values=values, status_code=400)
            from app.main import _render_template

            response = await _render_template(
                "forms/submit.html", request, user,
                extra={"title": form["name"], "form": form, "fields": fields, "errors": exc.errors, "submitted_values": values},
            )
            response.status_code = 400
            return response
        raise HTTPException(status_code=400, detail={"errors": exc.errors})

    if public:
        if html_submission:
            return _render_public_form(request, form, [], submit_action, success=True)
        return JSONResponse(status_code=201, content={"status": "ok", "message": "Your submission was received."}, headers={"Cache-Control": "no-store"})

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


# Public sharing is a separate bearer-token capability, never a portal session.
def _share_link_url(request: Request, form: dict[str, Any], token: str) -> str:
    return str(request.url_for("public_shared_form", slug=form["slug"]).include_query_params(token=token))


def _reveal_share_link(request: Request, form: dict[str, Any], link: dict[str, Any]) -> dict[str, Any]:
    """Replace the stored ciphertext with the usable URL (None when unrecoverable)."""
    revealed = {k: v for k, v in link.items() if k not in ("token_encrypted", "token_hash")}
    revealed["url"] = None
    encrypted = link.get("token_encrypted")
    if encrypted and not link.get("revoked_at"):
        try:
            revealed["url"] = _share_link_url(request, form, decrypt_secret(encrypted, allow_plaintext=False))
        except (ValueError, InvalidTag):
            # Encryption key changed since creation; the link still works but
            # can no longer be displayed.
            pass
    return revealed


@router.post("/api/forms/{form_id}/share-links", status_code=201)
async def api_create_share_link(form_id: int, payload: FormShareLinkCreate,
                                request: Request, response: Response, user: dict[str, Any] = Depends(get_current_user)):
    form = await _get_admin_form(form_id, user)
    response.headers["Cache-Control"] = "no-store"
    if not await companies_repo.get_company_by_id(payload.company_id):
        raise HTTPException(status_code=400, detail="Choose an existing company.")
    token = secrets.token_urlsafe(32)
    expires_at = payload.expires_at.astimezone(timezone.utc).replace(tzinfo=None) if payload.expires_at else None
    link = await forms_repo.create_share_link(
        form_id=form_id, company_id=payload.company_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(), token_encrypted=encrypt_secret(token),
        expires_at=expires_at, created_by=user.get("id"),
    )
    link = {k: v for k, v in link.items() if k != "token_encrypted"}
    return {**link, "url": _share_link_url(request, form, token)}


@router.get("/api/forms/{form_id}/share-links")
async def api_list_share_links(form_id: int, request: Request, response: Response,
                               user: dict[str, Any] = Depends(get_current_user)):
    form = await _get_admin_form(form_id, user)
    response.headers["Cache-Control"] = "no-store"
    return [_reveal_share_link(request, form, link) for link in await forms_repo.list_share_links(form_id)]


@router.delete("/api/forms/{form_id}/share-links/{link_id}")
async def api_revoke_share_link(form_id: int, link_id: int, user: dict[str, Any] = Depends(get_current_user)):
    await _get_admin_form(form_id, user)
    await forms_repo.revoke_share_link(form_id, link_id)
    return {"ok": True}


async def _revealed_share_link(form_id: int, link_id: int, request: Request, user: dict[str, Any]):
    form = await _get_admin_form(form_id, user)
    link = await forms_repo.get_share_link(form_id, link_id)
    revealed = _reveal_share_link(request, form, link) if link else None
    if not revealed or not revealed["url"]:
        raise HTTPException(status_code=404, detail="This link cannot be shown.")
    return form, revealed


def _qr_png(data: str) -> bytes:
    """Render locally so share tokens are never sent to a third-party QR service."""
    buffer = BytesIO()
    qrcode.make(data, border=4, box_size=10).save(buffer, format="PNG")
    return buffer.getvalue()


@router.get("/admin/custom-forms/{form_id}/sharing/{link_id}/qr.png", include_in_schema=False)
async def share_link_qr(form_id: int, link_id: int, request: Request,
                        download: bool = False, user: dict[str, Any] = Depends(get_current_user)):
    form, link = await _revealed_share_link(form_id, link_id, request, user)
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{form["slug"]}-link-{link["id"]}-qr.png"'
    return Response(content=_qr_png(link["url"]), media_type="image/png", headers=headers)


@router.get("/admin/custom-forms/{form_id}/sharing", include_in_schema=False)
async def share_links_page(form_id: int, request: Request, user: dict[str, Any] = Depends(get_current_user)):
    return await _share_links_page(form_id, request, user)


async def _share_links_page(form_id, request, user, *, error=None):
    form = await _get_admin_form(form_id, user)
    created = request.query_params.get("created", "")
    from app.main import _render_template
    response = await _render_template("admin/custom_form_sharing.html", request, user, extra={
        "title": "Share form", "form": form, "error": error,
        "created_link_id": int(created) if created.isdigit() else None,
        "links": [_reveal_share_link(request, form, link) for link in await forms_repo.list_share_links(form_id)],
        "companies": await companies_repo.list_companies(),
    })
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/admin/custom-forms/{form_id}/sharing", include_in_schema=False)
async def save_share_links_page(form_id: int, request: Request, user: dict[str, Any] = Depends(get_current_user)):
    await _get_admin_form(form_id, user)
    data = await request.form()
    try:
        if data.get("action") == "revoke":
            await api_revoke_share_link(form_id, int(data.get("link_id") or 0), user)
            return flash_redirect(f"/admin/custom-forms/{form_id}/sharing", "Link revoked.", "success")
        expiry = str(data.get("expires_at") or "").strip()
        if data.get("expiry_mode") != "never" and not expiry:
            raise ValueError("Choose an expiry date or Never expires.")
        payload = FormShareLinkCreate(
            company_id=int(data.get("company_id") or 0),
            expires_at=None if data.get("expiry_mode") == "never" else expiry + "+00:00",
        )
        result = await api_create_share_link(form_id, payload, request, Response(), user)
    except (ValueError, ValidationError):
        return await _share_links_page(form_id, request, user, error="Choose a company and a future expiry date (UTC), or Never expires.")
    except HTTPException as exc:
        if exc.status_code != 400:
            raise
        return await _share_links_page(form_id, request, user, error=str(exc.detail))
    return flash_redirect(f"/admin/custom-forms/{form_id}/sharing?created={result['id']}",
                          "Link created. Its link and QR code can be shown again at any time.", "success")


async def _resolve_shared_form(slug: str, request: Request):
    token = request.query_params.get("token", "")
    if len(token) != 43:
        raise HTTPException(status_code=404, detail="This form link is unavailable or has expired.")
    link = await forms_repo.get_share_link_by_hash(hashlib.sha256(token.encode()).hexdigest())
    if not link or link.get("revoked_at"):
        raise HTTPException(status_code=404, detail="This form link is unavailable or has expired.")
    expiry = link.get("expires_at")
    if expiry:
        if isinstance(expiry, str):
            expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            raise HTTPException(status_code=404, detail="This form link is unavailable or has expired.")
    form = await forms_repo.get_form(int(link["form_id"]))
    if not form or form["slug"] != slug or not form.get("active"):
        raise HTTPException(status_code=404, detail="This form link is unavailable or has expired.")
    if not await companies_repo.get_company_by_id(int(link["company_id"])):
        raise HTTPException(status_code=404, detail="This form link is unavailable or has expired.")
    return form, link


def _render_public_form(request, form, fields, submit_action, *, status_code=200, **extra):
    from app.main import templates
    return templates.TemplateResponse(request, "forms/submit.html", {
        "request": request, "title": form["name"], "form": form, "fields": fields,
        "public_share": True, "submit_action": submit_action, **extra,
    }, status_code=status_code, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"})


@router.get("/public/forms/{slug}", name="public_shared_form", include_in_schema=False)
async def public_shared_form(slug: str, request: Request):
    form, link = await _resolve_shared_form(slug, request)
    return _render_public_form(request, form, await forms_repo.list_fields(form["id"]), str(request.url))


@router.post("/public/forms/{slug}", include_in_schema=False)
async def submit_shared_form(slug: str, request: Request):
    form, link = await _resolve_shared_form(slug, request)
    # Anonymous submissions must never impersonate the link creator or visitor.
    return await _submit_form_request(form, request, {"company_id": int(link["company_id"])},
                                      public=True, submit_action=str(request.url))
