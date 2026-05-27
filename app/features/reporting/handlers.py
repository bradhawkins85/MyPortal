"""Reporting handlers for the ``reporting`` feature pack."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from starlette.datastructures import FormData


_REPORTING_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _main():
    from app import main as main_module

    return main_module


def _reporting_message(value: str | None, *, max_length: int = 240) -> str | None:
    if not value:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


def _reporting_user_label(record: Any) -> str:
    first = (record.get("first_name") or "").strip()
    last = (record.get("last_name") or "").strip()
    name = (f"{first} {last}").strip()
    email = (record.get("email") or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return name or email or f"User #{record.get('id')}"


async def _list_reporting_eligible_users() -> list[dict[str, Any]]:
    from app.repositories import users as user_repo

    rows = await user_repo.list_users()
    eligible: list[dict[str, Any]] = []
    for record in rows or []:
        if record.get("is_super_admin"):
            continue
        try:
            user_id = int(record.get("id"))
        except (TypeError, ValueError):
            continue
        eligible.append({"id": user_id, "label": _reporting_user_label(record)})
    eligible.sort(key=lambda item: item["label"].lower())
    return eligible


async def _require_reporting_access(request: Request):
    from app.core.logging import log_error

    user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return None, False, redirect
    is_super_admin = bool(user.get("is_super_admin"))
    is_tech = await _main()._is_helpdesk_technician(user, request)
    if not (is_super_admin or is_tech):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reporting access requires super admin or helpdesk technician privileges.",
        )
    return user, is_super_admin, None


async def _resolve_user_can_run_report(user: Any, is_super_admin: bool, query_id: int) -> bool:
    from app.core.logging import log_error
    from app.repositories import reporting as reporting_repo

    if is_super_admin:
        return True
    user_id = user.get("id")
    if user_id is None:
        return False
    try:
        return await reporting_repo.user_has_permission(int(query_id), int(user_id))
    except Exception as exc:  # pragma: no cover - defensive
        log_error("Failed to check reporting permission", error=str(exc))
        return False


def _parse_reporting_form(form: FormData) -> dict[str, Any]:
    name = (form.get("name") or "").strip()
    slug = (form.get("slug") or "").strip().lower()
    description = (form.get("description") or "").strip() or None
    sql_query = (form.get("sql_query") or "").strip()
    raw_user_ids = form.getlist("permission_user_ids") if hasattr(form, "getlist") else []
    user_ids: list[int] = []
    for raw in raw_user_ids or []:
        try:
            user_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return {
        "name": name,
        "slug": slug,
        "description": description,
        "sql_query": sql_query,
        "user_ids": user_ids,
    }


def _validate_reporting_input(payload: dict[str, Any]) -> str | None:
    from app.services import reporting as reporting_service

    if not payload["name"]:
        return "Report name is required."
    if len(payload["name"]) > 255:
        return "Report name must be 255 characters or fewer."
    if not payload["slug"]:
        return "Slug is required."
    if len(payload["slug"]) > 120:
        return "Slug must be 120 characters or fewer."
    if not _REPORTING_SLUG_RE.match(payload["slug"]):
        return "Slug may only contain letters, digits, underscores, and hyphens."
    if not payload["sql_query"]:
        return "SQL query is required."
    if payload["description"] and len(payload["description"]) > 1000:
        return "Description must be 1000 characters or fewer."
    try:
        reporting_service.validate_select_query(payload["sql_query"])
    except reporting_service.ReportingQueryError as exc:
        return str(exc)
    return None


async def reporting_page(
    request: Request,
    report: int | None = Query(default=None),
    error: str | None = Query(default=None),
):
    from app.repositories import reporting as reporting_repo
    from app.services import audit as audit_service
    from app.services import reporting as reporting_service

    user, is_super_admin, redirect = await _require_reporting_access(request)
    if redirect:
        return redirect

    user_id = int(user.get("id")) if user.get("id") is not None else 0
    if is_super_admin:
        available = await reporting_repo.list_queries()
    else:
        available = await reporting_repo.list_queries_for_user(user_id)
    available_reports = [
        {
            "id": entry["id"],
            "name": entry["name"],
            "slug": entry.get("slug"),
        }
        for entry in available
    ]

    selected_report = None
    result = None
    error_message = _reporting_message(error)
    generated_at_iso: str | None = None
    if report is not None:
        record = await reporting_repo.get_query(int(report))
        if not record:
            error_message = "The requested report no longer exists."
        else:
            allowed = await _resolve_user_can_run_report(user, is_super_admin, record["id"])
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to run this report.",
                )
            selected_report = record
            try:
                result = await reporting_service.run_query(record["sql_query"])
                generated_at_iso = datetime.now(timezone.utc).isoformat()
                await audit_service.record(
                    action="reporting.report.run",
                    request=request,
                    user_id=user.get("id"),
                    entity_type="reporting_query",
                    entity_id=int(record["id"]),
                    metadata={"slug": record.get("slug")},
                )
            except reporting_service.ReportingQueryError as exc:
                error_message = f"Report query is invalid: {exc}"
            except Exception as exc:  # pragma: no cover - defensive
                from app.core.logging import log_error
                log_error("Reporting query execution failed", error=str(exc))
                error_message = f"Report failed to execute: {exc}"

    extra = {
        "title": "Reporting",
        "available_reports": available_reports,
        "selected_report": selected_report,
        "result": result or {"columns": [], "rows": [], "row_count": 0, "truncated": False},
        "generated_at_iso": generated_at_iso,
        "max_rows": reporting_service.MAX_RESULT_ROWS,
        "error_message": error_message,
        "can_admin_reporting": is_super_admin,
    }
    return await _main()._render_template("reporting/index.html", request, user, extra=extra)


async def reporting_export(request: Request, report_id: int, format: str = Query(default="csv")):
    from app.repositories import reporting as reporting_repo
    from app.services import audit as audit_service
    from app.services import reporting as reporting_service

    user, is_super_admin, redirect = await _require_reporting_access(request)
    if redirect:
        return redirect

    record = await reporting_repo.get_query(int(report_id))
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")
    allowed = await _resolve_user_can_run_report(user, is_super_admin, record["id"])
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to run this report.",
        )

    fmt = (format or "csv").strip().lower()
    if fmt not in {"csv", "json", "xml", "pdf"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported export format.",
        )

    try:
        result = await reporting_service.run_query(record["sql_query"])
    except reporting_service.ReportingQueryError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await audit_service.record(
        action="reporting.report.export",
        request=request,
        user_id=user.get("id"),
        entity_type="reporting_query",
        entity_id=int(record["id"]),
        metadata={"slug": record.get("slug"), "format": fmt, "row_count": result["row_count"]},
    )

    base_filename = (record.get("slug") or f"report-{record['id']}")
    columns = result["columns"]
    rows = result["rows"]

    if fmt == "csv":
        body = reporting_service.export_csv(columns, rows)
        return Response(
            content=body,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{base_filename}.csv"'},
        )
    if fmt == "json":
        body = reporting_service.export_json(columns, rows)
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{base_filename}.json"'},
        )
    if fmt == "xml":
        body = reporting_service.export_xml(columns, rows)
        return Response(
            content=body,
            media_type="application/xml; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{base_filename}.xml"'},
        )
    # PDF
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
    html = reporting_service.export_html_for_pdf(
        record.get("name") or "Report",
        record.get("description"),
        columns,
        rows,
        datetime.now(timezone.utc),
    )
    pdf_bytes = HTML(string=html, base_url=str(request.base_url)).write_pdf()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{base_filename}.pdf"'},
    )


async def admin_reporting(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    from app.repositories import reporting as reporting_repo

    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    records = await reporting_repo.list_queries()
    reports_payload: list[dict[str, Any]] = []
    for record in records:
        prepared = dict(record)
        prepared["updated_at_iso"] = _main()._to_iso(record.get("updated_at"))
        reports_payload.append(prepared)
    extra = {
        "title": "Reporting · Manage reports",
        "reports": reports_payload,
        "success_message": _reporting_message(success),
        "error_message": _reporting_message(error),
    }
    return await _main()._render_template("admin/reporting.html", request, user, extra=extra)


async def admin_reporting_new(request: Request, error: str | None = Query(default=None)):
    from app.services import reporting as reporting_service

    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    eligible = await _list_reporting_eligible_users()
    extra = {
        "title": "New report",
        "form_heading": "New report",
        "submit_label": "Create report",
        "form_action": "/admin/reporting",
        "report": {},
        "eligible_users": eligible,
        "granted_user_ids": set(),
        "max_rows": reporting_service.MAX_RESULT_ROWS,
        "error_message": _reporting_message(error),
    }
    return await _main()._render_template("admin/reporting_form.html", request, user, extra=extra)


async def admin_reporting_edit(
    request: Request, report_id: int, error: str | None = Query(default=None)
):
    from app.repositories import reporting as reporting_repo
    from app.services import reporting as reporting_service

    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    record = await reporting_repo.get_query(int(report_id))
    if not record:
        return RedirectResponse(
            url="/admin/reporting?error=Report+not+found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    eligible = await _list_reporting_eligible_users()
    granted_ids = set(await reporting_repo.list_permission_user_ids(int(report_id)))
    extra = {
        "title": f"Edit report · {record['name']}",
        "form_heading": f"Edit report · {record['name']}",
        "submit_label": "Save changes",
        "form_action": f"/admin/reporting/{int(report_id)}",
        "report": record,
        "eligible_users": eligible,
        "granted_user_ids": granted_ids,
        "max_rows": reporting_service.MAX_RESULT_ROWS,
        "error_message": _reporting_message(error),
    }
    return await _main()._render_template("admin/reporting_form.html", request, user, extra=extra)


async def admin_reporting_create(request: Request):
    from app.repositories import reporting as reporting_repo
    from app.services import audit as audit_service

    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    payload = _parse_reporting_form(form)
    error = _validate_reporting_input(payload)
    if error:
        encoded = urlencode({"error": error})
        return RedirectResponse(
            url=f"/admin/reporting/new?{encoded}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    existing = await reporting_repo.get_query_by_slug(payload["slug"])
    if existing:
        encoded = urlencode({"error": "That slug is already in use."})
        return RedirectResponse(
            url=f"/admin/reporting/new?{encoded}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    new_id = await reporting_repo.create_query(
        slug=payload["slug"],
        name=payload["name"],
        description=payload["description"],
        sql_query=payload["sql_query"],
        created_by=user.get("id"),
    )
    await reporting_repo.replace_permissions(int(new_id), payload["user_ids"])
    await audit_service.record(
        action="reporting.report.create",
        request=request,
        user_id=user.get("id"),
        entity_type="reporting_query",
        entity_id=int(new_id),
        after={
            "slug": payload["slug"],
            "name": payload["name"],
            "description": payload["description"],
            "permission_user_ids": payload["user_ids"],
        },
    )
    encoded = urlencode({"success": "Report created."})
    return RedirectResponse(
        url=f"/admin/reporting?{encoded}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_reporting_update(request: Request, report_id: int):
    from app.repositories import reporting as reporting_repo
    from app.services import audit as audit_service

    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    record = await reporting_repo.get_query(int(report_id))
    if not record:
        return RedirectResponse(
            url="/admin/reporting?error=Report+not+found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    form = await request.form()
    payload = _parse_reporting_form(form)
    error = _validate_reporting_input(payload)
    if error:
        encoded = urlencode({"error": error})
        return RedirectResponse(
            url=f"/admin/reporting/{int(report_id)}/edit?{encoded}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if payload["slug"] != record.get("slug"):
        clash = await reporting_repo.get_query_by_slug(payload["slug"])
        if clash and int(clash["id"]) != int(report_id):
            encoded = urlencode({"error": "That slug is already in use."})
            return RedirectResponse(
                url=f"/admin/reporting/{int(report_id)}/edit?{encoded}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
    before_snapshot = {
        "slug": record.get("slug"),
        "name": record.get("name"),
        "description": record.get("description"),
        "sql_query": record.get("sql_query"),
    }
    await reporting_repo.update_query(
        int(report_id),
        slug=payload["slug"],
        name=payload["name"],
        description=payload["description"],
        sql_query=payload["sql_query"],
    )
    await reporting_repo.replace_permissions(int(report_id), payload["user_ids"])
    await audit_service.record(
        action="reporting.report.update",
        request=request,
        user_id=user.get("id"),
        entity_type="reporting_query",
        entity_id=int(report_id),
        before=before_snapshot,
        after={
            "slug": payload["slug"],
            "name": payload["name"],
            "description": payload["description"],
            "sql_query": payload["sql_query"],
            "permission_user_ids": payload["user_ids"],
        },
    )
    encoded = urlencode({"success": "Report updated."})
    return RedirectResponse(
        url=f"/admin/reporting?{encoded}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_reporting_delete(request: Request, report_id: int):
    from app.repositories import reporting as reporting_repo
    from app.services import audit as audit_service

    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    record = await reporting_repo.get_query(int(report_id))
    if not record:
        return RedirectResponse(
            url="/admin/reporting?error=Report+not+found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    await reporting_repo.delete_query(int(report_id))
    await audit_service.record(
        action="reporting.report.delete",
        request=request,
        user_id=user.get("id"),
        entity_type="reporting_query",
        entity_id=int(report_id),
        before={
            "slug": record.get("slug"),
            "name": record.get("name"),
            "description": record.get("description"),
        },
    )
    encoded = urlencode({"success": f"Deleted report '{record.get('name')}'."})
    return RedirectResponse(
        url=f"/admin/reporting?{encoded}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


__all__ = [
    "reporting_page",
    "reporting_export",
    "admin_reporting",
    "admin_reporting_new",
    "admin_reporting_edit",
    "admin_reporting_create",
    "admin_reporting_update",
    "admin_reporting_delete",
]
