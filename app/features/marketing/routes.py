"""Marketing feature routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.logging import log_error
from app.repositories import company_memberships as membership_repo
from app.repositories import essential8 as essential8_repo
from app.repositories import marketing as marketing_repo
from app.security.flash import flash_redirect
from app.services import tickets as tickets_service

router = APIRouter(tags=["Marketing"])

_MARKETING_PERMISSION = "marketing.access"
_MARKETING_TAG = "marketing"
_MAIN_MODULE = None


def _main():
    global _MAIN_MODULE
    if _MAIN_MODULE is None:
        from app import main as main_module

        _MAIN_MODULE = main_module
    return _MAIN_MODULE


def _form_bool(form: Mapping[str, Any], key: str) -> bool:
    value = form.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "off"}
    return bool(value)


async def _require_marketing_access(
    request: Request,
) -> tuple[dict[str, Any] | None, RedirectResponse | None]:
    main_module = _main()
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return None, redirect
    if not user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if bool(user.get("is_super_admin")):
        return user, None
    try:
        user_id = int(user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied") from None
    has_access = await membership_repo.user_has_permission(user_id, _MARKETING_PERMISSION)
    if not has_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Marketing access required")
    return user, None


def _coerce_slug(raw_slug: str) -> str:
    normalised = marketing_repo.slugify(raw_slug)
    if not normalised:
        raise ValueError("Enter a valid slug using letters, numbers, and dashes.")
    if len(normalised) > 96:
        raise ValueError("Slug must be 96 characters or fewer.")
    return normalised


def _coerce_title(raw_title: str) -> str:
    title = str(raw_title or "").strip()
    if not title:
        raise ValueError("Enter a page title.")
    if len(title) > 160:
        raise ValueError("Title must be 160 characters or fewer.")
    return title


async def _build_essential8_help_mapping_controls() -> list[dict[str, Any]]:
    controls = await essential8_repo.list_essential8_controls()
    requirements = await essential8_repo.list_essential8_requirements()
    mapping_rows = {
        item["requirement_id"]: item
        for item in await essential8_repo.list_requirement_marketing_page_links()
    }
    requirements_by_control: dict[int, list[dict[str, Any]]] = {}
    for requirement in requirements:
        row = dict(requirement)
        mapping = mapping_rows.get(requirement["id"])
        row["selected_marketing_page_id"] = (
            mapping["marketing_page_id"] if mapping else None
        )
        requirements_by_control.setdefault(requirement["control_id"], []).append(row)
    result: list[dict[str, Any]] = []
    for control in controls:
        result.append(
            {
                **control,
                "requirements": requirements_by_control.get(control["id"], []),
            }
        )
    return result


@router.get("/marketing/{slug}", response_class=HTMLResponse)
async def marketing_landing_page(request: Request, slug: str):
    page = await marketing_repo.get_page_by_slug(marketing_repo.slugify(slug), published_only=True)
    if not page:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Marketing page not found")

    form_values = {
        "name": str(request.query_params.get("name") or "").strip(),
        "email": str(request.query_params.get("email") or "").strip(),
        "phone": str(request.query_params.get("phone") or "").strip(),
        "allow_marketing": str(request.query_params.get("allow_marketing") or "").strip() == "1",
        "allow_other_services": str(request.query_params.get("allow_other_services") or "").strip() == "1",
    }
    extra = {
        "title": page.get("title") or "Marketing",
        "marketing_page": page,
        "success_message": request.query_params.get("success"),
        "error_message": request.query_params.get("error"),
        "form_values": form_values,
    }
    context = await _main()._build_public_context(request, extra=extra)
    return _main().templates.TemplateResponse(context["request"], "marketing/public_page.html", context)


@router.post("/marketing/{slug}/contact", response_class=HTMLResponse)
async def marketing_submit_contact(request: Request, slug: str):
    page = await marketing_repo.get_page_by_slug(marketing_repo.slugify(slug), published_only=True)
    if not page:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Marketing page not found")

    form = await request.form()
    name = str(form.get("name") or "").strip()
    email = str(form.get("email") or "").strip()
    phone = str(form.get("phone") or "").strip()
    allow_marketing = _form_bool(form, "allow_marketing")
    allow_other_services = allow_marketing and _form_bool(form, "allow_other_services")

    if not name:
        return RedirectResponse(
            url=f"/marketing/{page['slug']}?error=Enter+your+name.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not email:
        return RedirectResponse(
            url=f"/marketing/{page['slug']}?error=Enter+your+email+address.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        validated_email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError:
        return RedirectResponse(
            url=f"/marketing/{page['slug']}?error=Enter+a+valid+email+address.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    ticket_status = await tickets_service.resolve_status_or_default(None)
    ticket_subject = f"Marketing enquiry: {page['title']}"
    ticket_description = (
        f"Marketing page: {page['slug']}\n"
        f"Name: {name}\n"
        f"Email: {validated_email}\n"
        f"Phone: {phone or 'Not provided'}\n"
        f"Agree to marketing: {'Yes' if allow_marketing else 'No'}\n"
        f"Agree to other products/services: {'Yes' if allow_other_services else 'No'}"
    )

    try:
        created_ticket = await tickets_service.create_ticket(
            subject=ticket_subject,
            description=ticket_description,
            requester_id=None,
            company_id=None,
            assigned_user_id=None,
            priority="normal",
            status=ticket_status,
            category=_MARKETING_TAG,
            module_slug=_MARKETING_TAG,
            external_reference=page["slug"],
            trigger_automations=True,
            initial_reply_author_id=None,
            requester_email=validated_email,
        )
        await marketing_repo.create_lead(
            page_id=page["id"],
            slug_snapshot=page["slug"],
            page_title_snapshot=page["title"],
            name=name,
            email=validated_email,
            phone=phone,
            allow_marketing=allow_marketing,
            allow_other_services=allow_other_services,
            ticket_id=created_ticket.get("id"),
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to process marketing contact form", slug=page["slug"], error=str(exc))
        return RedirectResponse(
            url=f"/marketing/{page['slug']}?error=We+couldn%27t+submit+your+request.+Please+try+again.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/marketing/{page['slug']}?success=Thanks%2C+we%27ve+received+your+request.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/admin/marketing", response_class=HTMLResponse)
async def admin_marketing_dashboard(request: Request):
    current_user, redirect = await _require_marketing_access(request)
    if redirect:
        return redirect
    pages = await marketing_repo.list_pages()
    leads = await marketing_repo.list_leads()
    extra: dict[str, Any] = {
        "title": "Marketing pages",
        "marketing_pages": pages,
        "marketing_leads": leads,
        "essential8_help_controls": [],
    }
    if bool(current_user.get("is_super_admin")):
        extra["essential8_help_controls"] = await _build_essential8_help_mapping_controls()
    return await _main()._render_template(
        "admin/marketing.html",
        request,
        current_user,
        extra=extra,
    )


@router.get("/admin/marketing/pages/{page_id}/edit", response_class=HTMLResponse)
async def admin_marketing_edit_page(page_id: int, request: Request):
    current_user, redirect = await _require_marketing_access(request)
    if redirect:
        return redirect
    page = await marketing_repo.get_page_by_id(page_id)
    if not page:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Marketing page not found")
    return await _main()._render_template(
        "admin/marketing_page_edit.html",
        request,
        current_user,
        extra={
            "title": f"Edit — {page['title']}",
            "marketing_page": page,
        },
    )


@router.post("/admin/marketing/pages", response_class=HTMLResponse)
async def admin_marketing_create_page(request: Request):
    current_user, redirect = await _require_marketing_access(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        slug = _coerce_slug(str(form.get("slug") or ""))
        title = _coerce_title(str(form.get("title") or ""))
        subtitle = str(form.get("subtitle") or "").strip() or None
        intro_text = str(form.get("intro_text") or "").strip() or None
        is_published = _form_bool(form, "is_published")
        await marketing_repo.create_page(
            slug=slug,
            title=title,
            subtitle=subtitle,
            intro_text=intro_text,
            is_published=is_published,
        )
    except ValueError as exc:
        extra: dict[str, Any] = {
            "title": "Marketing pages",
            "marketing_pages": await marketing_repo.list_pages(),
            "marketing_leads": await marketing_repo.list_leads(),
            "error_message": str(exc),
            "essential8_help_controls": [],
        }
        if bool(current_user.get("is_super_admin")):
            extra["essential8_help_controls"] = await _build_essential8_help_mapping_controls()
        return await _main()._render_template(
            "admin/marketing.html",
            request,
            current_user,
            extra=extra,
        )
    return flash_redirect("/admin/marketing", "Marketing page created.", "success")


@router.post("/admin/marketing/pages/{page_id}", response_class=HTMLResponse)
async def admin_marketing_update_page(page_id: int, request: Request):
    current_user, redirect = await _require_marketing_access(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        slug = _coerce_slug(str(form.get("slug") or ""))
        title = _coerce_title(str(form.get("title") or ""))
        subtitle = str(form.get("subtitle") or "").strip() or None
        intro_text = str(form.get("intro_text") or "").strip() or None
        is_published = _form_bool(form, "is_published")
        await marketing_repo.update_page(
            page_id,
            slug=slug,
            title=title,
            subtitle=subtitle,
            intro_text=intro_text,
            is_published=is_published,
        )
    except ValueError as exc:
        page = await marketing_repo.get_page_by_id(page_id)
        if not page:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Marketing page not found") from exc
        return await _main()._render_template(
            "admin/marketing_page_edit.html",
            request,
            current_user,
            extra={
                "title": f"Edit — {page['title']}",
                "marketing_page": page,
                "error_message": str(exc),
            },
        )
    return flash_redirect(f"/admin/marketing/pages/{page_id}/edit", "Marketing page updated.", "success")


@router.post("/admin/marketing/essential8-help-links", response_class=HTMLResponse)
async def admin_marketing_update_essential8_help_links(request: Request):
    current_user, redirect = await _require_marketing_access(request)
    if redirect:
        return redirect
    if not bool(current_user.get("is_super_admin")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")

    pages = await marketing_repo.list_pages()
    requirements = await essential8_repo.list_essential8_requirements()
    valid_page_ids = {page["id"] for page in pages}
    mappings: dict[int, int | None] = {}
    form = await request.form()
    for requirement in requirements:
        field_name = f"requirement_{requirement['id']}"
        raw_value = str(form.get(field_name) or "").strip()
        if not raw_value:
            mappings[requirement["id"]] = None
            continue
        try:
            page_id = int(raw_value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid marketing page selection for requirement {requirement['id']}",
            ) from exc
        if page_id not in valid_page_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown marketing page selection for requirement {requirement['id']}",
            )
        mappings[requirement["id"]] = page_id
    await essential8_repo.replace_requirement_marketing_page_links(mappings)
    return flash_redirect("/admin/marketing", "Essential 8 help links updated.", "success")


@router.post("/admin/marketing/pages/{page_id}/delete", response_class=HTMLResponse)
async def admin_marketing_delete_page(page_id: int, request: Request):
    _, redirect = await _require_marketing_access(request)
    if redirect:
        return redirect
    await marketing_repo.delete_page(page_id)
    return flash_redirect("/admin/marketing", "Marketing page deleted.", "success")


@router.post("/admin/marketing/pages/{page_id}/sections", response_class=HTMLResponse)
async def admin_marketing_create_section(page_id: int, request: Request):
    current_user, redirect = await _require_marketing_access(request)
    if redirect:
        return redirect
    form = await request.form()
    title = str(form.get("title") or "").strip()
    content_text = str(form.get("content_text") or "").strip()
    anchor_raw = str(form.get("anchor_slug") or "").strip()
    sort_order_raw = str(form.get("sort_order") or "").strip()
    try:
        sort_order = int(sort_order_raw) if sort_order_raw else 0
    except ValueError:
        sort_order = 0
    if not title or not content_text:
        page = await marketing_repo.get_page_by_id(page_id)
        if not page:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Marketing page not found")
        return await _main()._render_template(
            "admin/marketing_page_edit.html",
            request,
            current_user,
            extra={
                "title": f"Edit — {page['title']}",
                "marketing_page": page,
                "error_message": "Section title and content are required.",
            },
        )
    anchor_slug = marketing_repo.slugify(anchor_raw or title)
    if not anchor_slug:
        anchor_slug = f"section-{page_id}"
    await marketing_repo.create_section(
        page_id=page_id,
        title=title,
        anchor_slug=anchor_slug,
        content_text=content_text,
        sort_order=sort_order,
    )
    return flash_redirect(f"/admin/marketing/pages/{page_id}/edit", "Section added.", "success")


@router.post("/admin/marketing/sections/{section_id}/delete", response_class=HTMLResponse)
async def admin_marketing_delete_section(section_id: int, request: Request):
    _, redirect = await _require_marketing_access(request)
    if redirect:
        return redirect
    section = await marketing_repo.get_section_by_id(section_id)
    await marketing_repo.delete_section(section_id)
    if section:
        return flash_redirect(f"/admin/marketing/pages/{section['page_id']}/edit", "Section deleted.", "success")
    return flash_redirect("/admin/marketing", "Section deleted.", "success")


__all__ = ["router"]
