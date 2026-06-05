"""Report handlers for the ``reports`` feature pack."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse

from app.security.flash import flash_redirect


def _main():
    from app import main as main_module

    return main_module


def _can_configure_report(user: Any, membership: Any) -> bool:
    if user.get("is_super_admin"):
        return True
    return bool(membership and membership.get("is_admin"))


async def _load_report_context(request: Request):
    from app.repositories import companies as company_repo
    from app.repositories import user_companies as user_company_repo

    user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return user, None, None, None, redirect
    company_id_raw = user.get("company_id")
    if company_id_raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No company associated with the current user",
        )
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid company identifier",
        ) from exc
    membership = await user_company_repo.get_user_company(user["id"], company_id)
    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, None


def _delete_cover_image_file(relative_path: str) -> None:
    private_uploads_path = _main()._private_uploads_path
    try:
        base = private_uploads_path.parent.resolve()
        candidate = (base / relative_path).resolve()
        candidate.relative_to(base)
        candidate.unlink(missing_ok=True)
    except (ValueError, OSError):  # pragma: no cover - defensive
        pass


async def company_overview_report_page(request: Request):
    from app.services import reports as reports_service

    user, membership, company, company_id, redirect = await _load_report_context(request)
    if redirect:
        return redirect
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    report = await reports_service.build_company_report(company_id)
    extra = {
        "title": "Company overview report",
        "report": report,
        "company": company,
        "can_configure_report": _can_configure_report(user, membership),
    }
    return await _main()._render_template("reports/index.html", request, user, extra=extra)


async def company_overview_report_pdf(request: Request):
    from fastapi.responses import StreamingResponse

    from app.services import audit as audit_service
    from app.services import reports as reports_service

    user, _membership, company, company_id, redirect = await _load_report_context(request)
    if redirect:
        return redirect
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    try:
        from weasyprint import HTML  # type: ignore
    except (ImportError, OSError) as exc:  # pragma: no cover - depends on system packages
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "PDF export requires WeasyPrint and its native dependencies. "
                "See https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation"
            ),
        ) from exc

    from app.repositories import site_settings as site_settings_repo

    pdf_cover_image_data_uri: str | None = None
    private_uploads_path = _main()._private_uploads_path
    cover_image_path = await site_settings_repo.get_pdf_cover_image()
    if cover_image_path:
        cover_file = (private_uploads_path.parent / cover_image_path).resolve()
        uploads_root = private_uploads_path.parent.resolve()
        try:
            cover_file.relative_to(uploads_root)
            if cover_file.is_file():
                suffix = cover_file.suffix.lower().lstrip(".")
                mime = {
                    "jpg": "image/jpeg",
                    "jpeg": "image/jpeg",
                    "png": "image/png",
                    "gif": "image/gif",
                    "webp": "image/webp",
                }.get(suffix, "image/jpeg")
                encoded = base64.b64encode(cover_file.read_bytes()).decode("ascii")
                pdf_cover_image_data_uri = f"data:{mime};base64,{encoded}"
        except (ValueError, OSError):
            pass

    report = await reports_service.build_company_report(company_id)
    base_context = await _main()._build_base_context(
        request,
        user,
        extra={
            "report": report,
            "company": company,
            "title": "Company overview report",
            "pdf_cover_image_data_uri": pdf_cover_image_data_uri,
        },
    )
    template = _main().templates.get_template("reports/pdf.html")
    rendered_html = template.render(base_context)

    await audit_service.log_action(
        action="report.company_overview.export_pdf",
        user_id=user.get("id"),
        entity_type="company",
        entity_id=company_id,
        metadata={"company_id": company_id},
        request=request,
    )

    pdf_bytes = HTML(
        string=rendered_html,
        base_url=str(request.base_url),
    ).write_pdf()

    safe_name = "".join(
        ch if ch.isalnum() or ch in (" ", "-", "_") else "_"
        for ch in (company.get("name") or f"company_{company_id}")
    ).strip().replace(" ", "_") or f"company_{company_id}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"company_overview_{safe_name}_{timestamp}.pdf"

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


async def company_overview_report_settings_page(request: Request):
    from app.services import reports as reports_service

    user, membership, company, company_id, redirect = await _load_report_context(request)
    if redirect:
        return redirect
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if not _can_configure_report(user, membership):
        return RedirectResponse(
            url="/reports/company-overview", status_code=status.HTTP_303_SEE_OTHER
        )
    visibility = await reports_service.get_section_visibility(company_id)
    detail_visibility = await reports_service.get_section_detail_visibility(company_id)
    report_settings = await reports_service.get_company_report_settings(company_id)
    section_order: list[str] | None = report_settings.get("section_order")
    all_sections = list(reports_service.REPORT_SECTIONS)
    if section_order:
        key_to_section = {s.key: s for s in all_sections}
        ordered = [key_to_section[k] for k in section_order if k in key_to_section]
        remaining = [s for s in all_sections if s.key not in set(section_order)]
        all_sections = ordered + remaining
    extra = {
        "title": "Report sections",
        "company": company,
        "sections": all_sections,
        "visibility": visibility,
        "detail_visibility": detail_visibility,
        "auto_hide_empty": report_settings.get("auto_hide_empty", True),
    }
    return await _main()._render_template("reports/settings.html", request, user, extra=extra)


async def company_overview_report_settings_save(request: Request):
    from app.services import audit as audit_service
    from app.services import reports as reports_service

    user, membership, company, company_id, redirect = await _load_report_context(request)
    if redirect:
        return redirect
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if not _can_configure_report(user, membership):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to configure reports.",
        )
    form = await request.form()
    enabled_keys = set(form.getlist("sections"))
    preferences = {
        section.key: (section.key in enabled_keys)
        for section in reports_service.REPORT_SECTIONS
    }
    await reports_service.save_section_visibility(company_id, preferences)
    detailed_keys = set(form.getlist("detailed_sections"))
    detail_preferences = {
        section.key: (section.key in detailed_keys and section.key in enabled_keys)
        for section in reports_service.REPORT_SECTIONS
    }
    await reports_service.save_section_detail_visibility(company_id, detail_preferences)
    auto_hide_empty = form.get("auto_hide_empty") == "1"
    raw_order = form.get("section_order", "")
    section_order_list: list[str] | None = (
        [k for k in raw_order.split(",") if k] if raw_order else None
    )
    await reports_service.save_company_report_settings(
        company_id, auto_hide_empty, section_order_list
    )
    await audit_service.log_action(
        action="report.company_overview.configure",
        user_id=user.get("id"),
        entity_type="company",
        entity_id=company_id,
        metadata={
            "enabled_sections": sorted(enabled_keys),
            "detailed_sections": sorted(detailed_keys & enabled_keys),
            "auto_hide_empty": auto_hide_empty,
        },
        request=request,
    )
    return RedirectResponse(
        url="/reports/company-overview", status_code=status.HTTP_303_SEE_OTHER
    )


async def admin_report_cover_image_page(request: Request):
    from app.repositories import site_settings as site_settings_repo

    user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    current_image = await site_settings_repo.get_pdf_cover_image()
    extra = {
        "title": "PDF cover image",
        "current_image": current_image,
    }
    return await _main()._render_template("admin/report_cover_image.html", request, user, extra=extra)


async def admin_report_cover_image_upload(request: Request, image: UploadFile = File(None)):
    from app.repositories import site_settings as site_settings_repo
    from app.services import audit as audit_service
    from app.services.file_storage import store_report_cover_image

    user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")

    if image is None or not image.filename:
        return flash_redirect("/admin/reports/pdf-cover-image", "No file selected", "error")

    private_uploads_path = _main()._private_uploads_path
    try:
        relative_path, _dest = await store_report_cover_image(
            upload=image,
            uploads_root=private_uploads_path,
        )
    except HTTPException as exc:
        return flash_redirect("/admin/reports/pdf-cover-image", exc.detail, "error")

    previous = await site_settings_repo.get_pdf_cover_image()
    if previous:
        _delete_cover_image_file(previous)

    await site_settings_repo.set_pdf_cover_image(relative_path)
    await audit_service.log_action(
        action="admin.report.pdf_cover_image.upload",
        user_id=user.get("id"),
        entity_type="site_settings",
        entity_id=1,
        metadata={"path": relative_path},
        request=request,
    )
    return flash_redirect("/admin/reports/pdf-cover-image", "Cover image updated", "success")


async def admin_report_cover_image_delete(request: Request):
    from app.repositories import site_settings as site_settings_repo
    from app.services import audit as audit_service

    user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")

    current = await site_settings_repo.get_pdf_cover_image()
    if current:
        _delete_cover_image_file(current)
    await site_settings_repo.set_pdf_cover_image(None)
    await audit_service.log_action(
        action="admin.report.pdf_cover_image.delete",
        user_id=user.get("id"),
        entity_type="site_settings",
        entity_id=1,
        metadata={},
        request=request,
    )
    return flash_redirect("/admin/reports/pdf-cover-image", "Cover image removed", "success")


async def admin_report_cover_image_preview(request: Request):
    from app.repositories import site_settings as site_settings_repo

    user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    private_uploads_path = _main()._private_uploads_path
    cover_image_path = await site_settings_repo.get_pdf_cover_image()
    if not cover_image_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No cover image set")
    cover_file = (private_uploads_path.parent / cover_image_path).resolve()
    uploads_root = private_uploads_path.parent.resolve()
    try:
        cover_file.relative_to(uploads_root)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path") from exc
    if not cover_file.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cover image not found")
    return FileResponse(cover_file, headers={"Cache-Control": "no-store"})
