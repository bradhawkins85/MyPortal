"""Message template page routes for the ``message_templates`` feature pack."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import message_templates as message_templates_service


router = APIRouter(tags=["Message Templates"])

MESSAGE_TEMPLATE_CONTENT_TYPES: tuple[tuple[str, str], ...] = (
    ("text/plain", "Plain text"),
    ("text/html", "HTML"),
)


def _main():
    from app import main as main_module

    return main_module


def _content_type_options() -> list[dict[str, str]]:
    return [
        {"value": value, "label": label}
        for value, label in MESSAGE_TEMPLATE_CONTENT_TYPES
    ]


@router.get("/admin/message-templates", response_class=HTMLResponse)
async def admin_message_templates(
    request: Request,
    search: str | None = Query(default=None),
    content_type: str | None = Query(default=None),
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    search_value = (search or "").strip()
    if len(search_value) > 120:
        search_value = search_value[:120]
    content_type_value = (content_type or "").strip().lower()
    if content_type_value not in {value for value, _ in MESSAGE_TEMPLATE_CONTENT_TYPES}:
        content_type_value = ""

    try:
        template_records = await message_templates_service.list_templates(
            search=search_value or None,
            content_type=content_type_value or None,
            limit=500,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        main_module.log_error("Failed to load message templates", error=str(exc))
        template_records = []

    templates_payload: list[dict[str, Any]] = []
    for record in template_records:
        prepared = dict(record)
        prepared["created_at_iso"] = main_module._to_iso(record.get("created_at"))
        prepared["updated_at_iso"] = main_module._to_iso(record.get("updated_at"))
        templates_payload.append(prepared)

    extra = {
        "title": "Message templates",
        "templates": templates_payload,
        "filters": {
            "search": search_value,
            "content_type": content_type_value or "",
        },
        "content_type_options": _content_type_options(),
        "success_message": main_module._sanitize_message(success),
        "error_message": main_module._sanitize_message(error),
    }

    return await main_module._render_template(
        "admin/message_templates.html",
        request,
        current_user,
        extra=extra,
    )


@router.get("/admin/message-templates/new", response_class=HTMLResponse)
async def admin_message_templates_new(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    extra = {
        "title": "New message template",
        "page_title": "New message template",
        "form_heading": "New template",
        "submit_label": "Save template",
        "show_reset_button": True,
        "template": {},
        "content_type_options": _content_type_options(),
        "default_content_type": "text/plain",
    }

    return await main_module._render_template(
        "admin/message_template_form.html",
        request,
        current_user,
        extra=extra,
    )


@router.get("/admin/message-templates/{template_id}/edit", response_class=HTMLResponse)
async def admin_message_templates_edit(request: Request, template_id: int):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    template_record = await message_templates_service.get_template(template_id)
    if not template_record:
        message = "Template not found."
        encoded = urlencode({"error": message})
        return RedirectResponse(
            url=f"/admin/message-templates?{encoded}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    extra = {
        "title": f"Edit {template_record.get('name') or 'template'}",
        "page_title": "Edit message template",
        "form_heading": "Edit template",
        "submit_label": "Update template",
        "show_reset_button": False,
        "template": template_record,
        "content_type_options": _content_type_options(),
        "default_content_type": template_record.get("content_type") or "text/plain",
    }

    return await main_module._render_template(
        "admin/message_template_form.html",
        request,
        current_user,
        extra=extra,
    )


__all__ = [
    "MESSAGE_TEMPLATE_CONTENT_TYPES",
    "admin_message_templates",
    "admin_message_templates_edit",
    "admin_message_templates_new",
    "router",
]
