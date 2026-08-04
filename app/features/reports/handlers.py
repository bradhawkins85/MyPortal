"""Report handlers for the ``reports`` feature pack."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

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

    user, redirect = await _main()._require_menu_page_access(
        request,
        "menu.reports",
        detail="Reports access permission required",
    )
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


def _safe_export_filename(name: str | None, company_id: int) -> str:
    safe_name = "".join(
        ch if ch.isalnum() or ch in (" ", "-", "_") else "_"
        for ch in (name or f"company_{company_id}")
    ).strip().replace(" ", "_") or f"company_{company_id}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"company_overview_layout_{safe_name}_{timestamp}.json"


def _layout_export_payload(
    company: dict[str, Any], company_id: int, layout: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "type": "myportal.company_overview_layout",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_company": {"id": company_id, "name": company.get("name")},
        "layout": layout,
    }


def _layout_from_import_payload(payload: Any) -> Any:
    if isinstance(payload, dict) and payload.get("type") == "myportal.company_overview_layout":
        return payload.get("layout")
    if isinstance(payload, list):
        return payload
    raise ValueError("Import file must be a company overview layout export.")

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
    from app.services import company_report_layout

    user, membership, company, company_id, redirect = await _load_report_context(request)
    if redirect:
        return redirect
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    report = await company_report_layout.build(company_id, company)
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
    from app.services import company_report_layout

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

    report = await company_report_layout.build(company_id, company)
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
    from app.services import company_report_layout

    user, membership, company, company_id, redirect = await _load_report_context(request)
    if redirect:
        return redirect
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if not _can_configure_report(user, membership):
        return RedirectResponse(
            url="/reports/company-overview", status_code=status.HTTP_303_SEE_OTHER
        )
    layout = await company_report_layout.get_layout(company_id)
    queries = await company_report_layout.available_queries()
    extra = {
        "title": "Report designer",
        "company": company,
        "layout": layout,
        "reporting_queries": queries,
    }
    return await _main()._render_template("reports/settings.html", request, user, extra=extra)


async def company_overview_report_settings_save(request: Request):
    from app.services import audit as audit_service
    from app.services import company_report_layout

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
    try:
        raw_layout = json.loads(str(form.get("layout_json") or "[]"))
        saved_layout = await company_report_layout.save_layout(company_id, raw_layout)
    except (json.JSONDecodeError, ValueError) as exc:
        return flash_redirect("/reports/company-overview/settings", str(exc), "error")
    await audit_service.log_action(
        action="report.company_overview.configure",
        user_id=user.get("id"),
        entity_type="company",
        entity_id=company_id,
        metadata={
            "rows": len(saved_layout),
            "columns": sum(len(row.get("columns", [])) for row in saved_layout),
        },
        request=request,
    )
    return RedirectResponse(
        url="/reports/company-overview", status_code=status.HTTP_303_SEE_OTHER
    )


async def company_overview_report_settings_export(request: Request):
    from app.services import audit as audit_service
    from app.services import company_report_layout

    user, membership, company, company_id, redirect = await _load_report_context(request)
    if redirect:
        return redirect
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if not _can_configure_report(user, membership):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to export report layouts.",
        )
    layout = await company_report_layout.get_layout(company_id)
    await audit_service.log_action(
        action="report.company_overview.export_layout",
        user_id=user.get("id"),
        entity_type="company",
        entity_id=company_id,
        metadata={"rows": len(layout)},
        request=request,
    )
    return JSONResponse(
        _layout_export_payload(company, company_id, layout),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_safe_export_filename(company.get("name"), company_id)}"'
            )
        },
    )


async def company_overview_report_settings_import(request: Request):
    from starlette.datastructures import UploadFile as StarletteUploadFile

    from app.services import audit as audit_service
    from app.services import company_report_layout

    user, membership, company, company_id, redirect = await _load_report_context(request)
    if redirect:
        return redirect
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if not _can_configure_report(user, membership):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to import report layouts.",
        )
    form = await request.form()
    raw_payload = str(form.get("layout_import_json") or "").strip()
    upload = form.get("layout_import_file")
    if isinstance(upload, StarletteUploadFile) and upload.filename:
        raw_bytes = await upload.read()
        raw_payload = raw_bytes.decode("utf-8-sig")
    if not raw_payload:
        return flash_redirect(
            "/reports/company-overview/settings",
            "Choose a report layout JSON file or paste exported JSON.",
            "error",
        )
    try:
        imported = _layout_from_import_payload(json.loads(raw_payload))
        saved_layout = await company_report_layout.save_layout(company_id, imported)
    except UnicodeDecodeError:
        return flash_redirect(
            "/reports/company-overview/settings",
            "Import file must be UTF-8 encoded JSON.",
            "error",
        )
    except (json.JSONDecodeError, ValueError) as exc:
        return flash_redirect("/reports/company-overview/settings", str(exc), "error")
    await audit_service.log_action(
        action="report.company_overview.import_layout",
        user_id=user.get("id"),
        entity_type="company",
        entity_id=company_id,
        metadata={
            "rows": len(saved_layout),
            "columns": sum(len(row.get("columns", [])) for row in saved_layout),
        },
        request=request,
    )
    return flash_redirect("/reports/company-overview/settings", "Report layout imported.", "success")


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
