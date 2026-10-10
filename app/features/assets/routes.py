"""Assets routes for the ``assets`` feature pack."""

from __future__ import annotations

import csv
import io
import ipaddress
import json
from urllib.parse import urlparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from app.core.logging import log_info
from app.repositories import asset_custom_fields as asset_custom_fields_repo
from app.repositories import assets as asset_repo
from app.repositories import asset_photos as asset_photo_repo
from app.repositories import rack_item_images as rack_image_repo
from app.repositories import shop as shop_repo
from app.repositories import expirations as expiration_repo
from app.repositories import users as users_repo
from app.repositories import companies as company_repo
from app.repositories import network_devices as network_devices_repo
from app.repositories import infrastructure as infrastructure_repo
from app.repositories import processes as processes_repo
from app.repositories import tickets as tickets_repo
from app.repositories import websites as websites_repo
from app.repositories import tray as tray_repo
from app.repositories import bcp as bcp_repo
from app.repositories import customer_content_audience as audience_repo
from app.repositories import roles as role_repo
from app.services import tray as tray_service
from app.services import hudu as hudu_service
from app.services import audit as audit_service
from app.services import knowledge_base as knowledge_base_service
from app.services import automations as automations_service
from app.services import asset_photos as asset_photo_service
from app.services import rack_item_images as rack_image_service
from app.services import asset_types
from app.services import rack_dashboard
from app.services import rack_item_types

router = APIRouter(tags=["Assets"])


async def _customer_role_can_view_asset(membership: dict[str, Any] | None, company_id: int, asset_id: int) -> bool:
    role_id = membership.get("role_id") if membership else None
    if role_id is None:
        # Audience rows were backfilled for every role when role-scoped
        # publication was introduced.  Only publications with no audience rows
        # can therefore use the narrow legacy roleless-membership compatibility
        # path; a newly scoped publication must never inherit broad menu access.
        return not await audience_repo.list_role_ids(company_id, "asset", asset_id)
    permissions = membership.get("menu_permissions") or membership.get("permissions") or {}
    if not isinstance(permissions, dict) or permissions.get("content.assets") not in {"read", "write"}:
        return False
    return await audience_repo.role_can_access(company_id, "asset", asset_id, int(role_id))

_RELATIONSHIP_TYPES = {
    "depends_on": "Depends on", "runs_on": "Runs on",
    "supported_by": "Supported by", "documented_by": "Documented by",
    "connected_to": "Connected to", "located_in": "Located in",
    "related_to": "Related to",
}

_RELATIONSHIP_TARGET_TYPES = {
    "asset", "knowledge_base_article", "ticket", "process_run", "website",
    "ip_network", "rack",
}


def _relationship_target(record_type: str, record: dict[str, Any]) -> dict[str, Any]:
    """Build the safe display contract used by the picker and relationship list."""
    record_id = int(record["id"])
    if record_type == "asset":
        return {"key": f"asset:{record_id}", "type": "Asset", "label": record.get("name") or "Unnamed asset", "context": record.get("type") or record.get("serial_number") or "Asset", "url": f"/assets/{record_id}"}
    if record_type == "knowledge_base_article":
        return {"key": f"knowledge_base_article:{record_id}", "type": "Knowledge base", "label": record.get("title") or "Untitled article", "context": record.get("lifecycle_status") or "Article", "url": f"/knowledge-base/articles/{record['slug']}"}
    if record_type == "ticket":
        number = record.get("ticket_number") or record_id
        return {"key": f"ticket:{record_id}", "type": "Ticket", "label": f"#{number} — {record.get('subject') or 'Untitled ticket'}", "context": record.get("status") or "Ticket", "url": f"/admin/tickets/{record_id}"}
    if record_type == "process_run":
        return {"key": f"process_run:{record_id}", "type": "Process", "label": record.get("template_name") or "Process run", "context": record.get("status") or "Run", "url": f"/api/processes/runs/{record_id}"}
    if record_type == "website":
        return {"key": f"website:{record_id}", "type": "Website", "label": record.get("name") or "Unnamed website", "context": record.get("url") or "Website", "url": f"/websites/{record_id}"}
    if record_type == "ip_network":
        return {"key": f"ip_network:{record_id}", "type": "Network", "label": record.get("name") or "Unnamed network", "context": record.get("cidr") or "Network", "url": f"/infrastructure#network-{record_id}"}
    return {"key": f"rack:{record_id}", "type": "Rack", "label": record.get("name") or "Unnamed rack", "context": record.get("location") or f"{record.get('unit_count') or '?'} units", "url": f"/infrastructure#rack-{record_id}"}


def _scanner_scope_from_form(form: Any) -> tuple[list[str], list[str]]:
    """Validate and canonicalise optional WAN and LAN scanner boundaries."""
    scopes: list[list[str]] = []
    for field, require_ipv4 in (("wan_cidrs", False), ("local_cidrs", True)):
        values: list[str] = []
        raw = str(form.get(field) or "").replace(",", " ")
        for item in raw.split():
            try:
                network = ipaddress.ip_network(item, strict=False)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"Invalid {field}: {item}")
            if require_ipv4 and network.version != 4:
                raise HTTPException(
                    status_code=422, detail="Local scan ranges must be IPv4 CIDRs"
                )
            canonical = str(network)
            if canonical not in values:
                values.append(canonical)
        scopes.append(values)
    return scopes[0], scopes[1]


_ASSET_TABLE_COLUMNS: list[dict[str, str]] = [
    {"key": "name", "label": "Name", "sort": "string", "priority": "essential"},
    {"key": "type", "label": "Type", "sort": "string"},
    {"key": "machine_type", "label": "Machine type", "sort": "string"},
    {"key": "serial_number", "label": "Serial number", "sort": "string"},
    {"key": "status", "label": "Status", "sort": "string", "priority": "essential"},
    {"key": "os_name", "label": "OS name", "sort": "string"},
    {"key": "cpu_name", "label": "CPU", "sort": "string"},
    {"key": "ram_gb", "label": "RAM (GB)", "sort": "number"},
    {"key": "hdd_size", "label": "Storage", "sort": "string"},
    {"key": "last_sync", "label": "Last sync", "sort": "date", "priority": "essential"},
    {"key": "boot_time", "label": "Boot time", "sort": "date"},
    {
        "key": "tray_agent_synced",
        "label": "TrayAgentID synced",
        "sort": "number",
        "field_type": "checkbox",
    },
    {"key": "motherboard_manufacturer", "label": "Motherboard", "sort": "string"},
    {"key": "form_factor", "label": "Form factor", "sort": "string"},
    {
        "key": "last_user",
        "label": "Last user",
        "sort": "string",
        "priority": "essential",
    },
    {"key": "approx_age", "label": "Approx age", "sort": "number"},
    {"key": "performance_score", "label": "Performance score", "sort": "number"},
    {"key": "warranty_status", "label": "Warranty status", "sort": "string"},
    {"key": "warranty_end_date", "label": "Warranty end", "sort": "date"},
]

# Customer output uses an allow-list. Never add notes, custom fields, linked
# tickets, integration IDs, credentials, or relationship metadata here.
_CUSTOMER_ASSET_FIELDS = (
    "id", "name", "type", "machine_type", "status", "os_name",
    "serial_number", "warranty_status", "warranty_end_date", "last_sync",
)


def _customer_asset(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record.get(key) for key in _CUSTOMER_ASSET_FIELDS}


def _main():
    from app import main as main_module

    return main_module


async def _load_asset_context(request: Request, permission_key: str = "menu.assets"):
    main_module = _main()
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return user, None, None, None, redirect

    is_super_admin = bool(user.get("is_super_admin"))
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
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid company identifier"
        ) from exc

    membership = await main_module._get_effective_company_membership(
        request, user["id"], company_id
    )
    can_view_assets = main_module._membership_menu_can(user, membership, permission_key)
    if not (is_super_admin or can_view_assets):
        return (
            user,
            membership,
            None,
            company_id,
            RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER),
        )

    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, None


@router.get("/assets", response_class=HTMLResponse)
async def assets_page(request: Request):
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_asset_context(request)
    if redirect:
        return redirect

    can_export_assets = main_module._membership_menu_can(
        user, membership, "menu.assets", write=True
    )
    can_write_assets = bool(user.get("is_super_admin")) or can_export_assets

    rows = await asset_repo.list_company_assets(company_id)
    if not can_write_assets:
        rows = [_customer_asset(dict(row)) for row in rows if bool(row.get("customer_visible")) and await _customer_role_can_view_asset(membership, company_id, int(row["id"]))]
    field_definitions = (
        await asset_custom_fields_repo.list_field_definitions()
        if user.get("is_super_admin") else []
    )

    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value).strip()
        return text or None

    def _format_number(value: Any) -> tuple[str | None, str]:
        if value is None:
            return None, ""
        if isinstance(value, str) and not value.strip():
            return None, ""
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError):
            text = _clean_text(value)
            return text, text or ""
        display = format(decimal_value.normalize(), "f")
        if "." in display:
            display = display.rstrip("0").rstrip(".")
        return display or "0", str(decimal_value)

    def _parse_iso(value: str | None) -> datetime | None:
        if not value:
            return None
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed

    prepared: list[dict[str, Any]] = []
    today = datetime.now(timezone.utc).date()
    recent_threshold = datetime.now(timezone.utc) - timedelta(days=30)
    recent_sync = 0
    expired_warranty = 0
    active_warranty = 0

    for row in rows:
        name = _clean_text(row.get("name")) or "Asset"
        record: dict[str, Any] = {
            "id": row.get("id"),
            "name": name,
            "type": asset_types.display_label(row),
            "asset_type_icon": asset_types.get(asset_types.effective(row)).icon,
            "reported_type": _clean_text(row.get("type")),
            "machine_type": _clean_text(row.get("machine_type")),
            "serial_number": _clean_text(row.get("serial_number")),
            "status": _clean_text(row.get("status")),
            "os_name": _clean_text(row.get("os_name")),
            "cpu_name": _clean_text(row.get("cpu_name")),
            "hdd_size": _clean_text(row.get("hdd_size")),
            "motherboard_manufacturer": _clean_text(
                row.get("motherboard_manufacturer")
            ),
            "form_factor": _clean_text(row.get("form_factor")),
            "last_user": _clean_text(row.get("last_user")),
            "warranty_status": _clean_text(row.get("warranty_status")),
            "syncro_asset_id": _clean_text(row.get("syncro_asset_id")),
            "tactical_asset_id": _clean_text(row.get("tactical_asset_id")),
        }

        ram_display, ram_sort = _format_number(row.get("ram_gb"))
        approx_display, approx_sort = _format_number(row.get("approx_age"))
        performance_display, performance_sort = _format_number(
            row.get("performance_score")
        )
        record["ram_gb"] = ram_display
        record["ram_gb_sort"] = ram_sort
        record["approx_age"] = approx_display
        record["approx_age_sort"] = approx_sort
        record["performance_score"] = performance_display
        record["performance_score_sort"] = performance_sort

        last_sync_iso = main_module._to_iso(row.get("last_sync"))
        record["last_sync"] = last_sync_iso
        record["last_sync_iso"] = last_sync_iso
        record["last_sync_sort"] = last_sync_iso or ""

        boot_time_iso = main_module._to_iso(row.get("boot_time"))
        record["boot_time"] = boot_time_iso
        record["boot_time_iso"] = boot_time_iso
        record["boot_time_sort"] = boot_time_iso or ""

        if last_sync_iso:
            parsed_last_sync = _parse_iso(last_sync_iso)
            if parsed_last_sync and parsed_last_sync >= recent_threshold:
                recent_sync += 1

        warranty_value = row.get("warranty_end_date")
        warranty_display: str | None
        warranty_sort = ""
        warranty_iso: str | None = None
        if isinstance(warranty_value, datetime):
            warranty_date = warranty_value.astimezone(timezone.utc).date()
            warranty_display = warranty_date.isoformat()
            warranty_iso = warranty_display
            warranty_sort = warranty_display
        elif isinstance(warranty_value, date):
            warranty_display = warranty_value.isoformat()
            warranty_iso = warranty_display
            warranty_sort = warranty_display
        else:
            warranty_display = _clean_text(warranty_value)
            if warranty_display:
                warranty_sort = warranty_display

        if warranty_iso:
            try:
                warranty_date_obj = date.fromisoformat(warranty_iso)
            except ValueError:
                warranty_date_obj = None
            if warranty_date_obj:
                if warranty_date_obj < today:
                    expired_warranty += 1
                else:
                    active_warranty += 1

        record["warranty_end_date"] = warranty_display
        record["warranty_end_sort"] = warranty_sort
        record["warranty_end_iso"] = warranty_iso

        prepared.append(record)

    asset_ids = [r["id"] for r in prepared if r.get("id")]
    cf_values_by_asset = await asset_custom_fields_repo.get_all_asset_field_values(
        asset_ids
    )
    tray_devices_by_asset = await tray_repo.list_active_devices_by_asset_ids(asset_ids)

    for record in prepared:
        tray_device = (
            tray_devices_by_asset.get(int(record["id"])) if record.get("id") else None
        )
        record["tray_device_uid"] = (
            tray_device.get("device_uid") if tray_device else None
        )
        record["tray_device_hostname"] = (
            tray_device.get("hostname") if tray_device else None
        )
        record["tray_agent_synced"] = bool(record["tray_device_uid"])
        record["can_open_chat"] = bool(
            record["tray_device_uid"] and main_module.settings.matrix_enabled
        )
        asset_id = record.get("id")
        asset_cf = cf_values_by_asset.get(asset_id, {})
        for field_def in field_definitions:
            key = f"cf_{field_def['id']}"
            record[key] = asset_cf.get(field_def["id"])

    custom_columns = [
        {
            "key": f"cf_{field_def['id']}",
            "label": field_def["display_name"] or field_def["name"],
            "sort": (
                "date"
                if field_def["field_type"] == "date"
                else ("number" if field_def["field_type"] == "checkbox" else "string")
            ),
            "field_type": field_def["field_type"],
        }
        for field_def in field_definitions
    ]
    all_columns = list(_ASSET_TABLE_COLUMNS) + custom_columns

    stats = {
        "total": len(prepared),
        "recent_sync": recent_sync,
        "expired_warranty": expired_warranty,
        "active_warranty": active_warranty,
    }
    has_asset_actions = bool(user.get("is_super_admin")) or any(
        bool(asset.get("can_open_chat")) for asset in prepared
    )

    extra = {
        "title": "Assets",
        "assets": prepared,
        "columns": all_columns,
        "company": company,
        "stats": stats,
        "has_assets": bool(prepared),
        "can_export_assets": can_export_assets,
        "can_write_assets": can_write_assets,
        "is_super_admin": bool(user.get("is_super_admin")),
        "has_asset_actions": has_asset_actions,
        "matrix_enabled": main_module.settings.matrix_enabled,
    }
    return await main_module._render_template(
        "assets/index.html", request, user, extra=extra
    )


async def _asset_write_context(request: Request):
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_asset_context(request)
    if redirect:
        return user, company, company_id, redirect
    if not (user.get("is_super_admin") or main_module._membership_menu_can(
        user, membership, "menu.assets", write=True
    )):
        raise HTTPException(status_code=403, detail="Asset write access required")
    return user, company, company_id, None


@router.get("/assets/integration-health", response_class=HTMLResponse,
            summary="View company integration health and reconciliation queue")
async def integration_health_page(request: Request):
    user, membership, company, company_id, redirect = await _load_asset_context(request)
    if redirect:
        return redirect
    main_module = _main()
    can_resolve = bool(user.get("is_super_admin")) or main_module._membership_menu_can(
        user, membership, "menu.assets", write=True
    )
    health = await asset_repo.list_integration_health(company_id)
    now = datetime.now(timezone.utc)
    for item in health:
        success = item.get("last_success")
        value = success.get("completed_at") if success else None
        if isinstance(value, datetime):
            value = value.replace(tzinfo=value.tzinfo or timezone.utc)
            item["is_stale"] = now - value > timedelta(hours=24)
        else:
            item["is_stale"] = True
        item["current_failure"] = bool(item.get("latest") and item["latest"].get("status") == "failed")
    return await main_module._render_template(
        "assets/integration_health.html", request, user,
        extra={"title": "Integration health", "company": company, "health": health,
               "queue": await asset_repo.list_company_reconciliation_queue(company_id),
               "can_resolve": can_resolve},
    )


@router.post("/assets/integration-health/reconciliation/{source_record_id}/reject",
             summary="Reject an ambiguous asset match")
async def reject_asset_reconciliation(request: Request, source_record_id: int):
    user, _company, company_id, redirect = await _asset_write_context(request)
    if redirect:
        return redirect
    if not await asset_repo.reject_reconciliation(company_id, source_record_id):
        raise HTTPException(status_code=404, detail="Pending match not found")
    await audit_service.record(
        action="asset.reconciliation.reject", request=request, user_id=int(user["id"]),
        entity_type="asset_source_record", entity_id=source_record_id,
        after={"source_record_id": source_record_id, "decision": "rejected"},
    )
    return _main().flash_redirect(
        "/assets/integration-health", "Integration match rejected.", "success"
    )


@router.get("/assets/new", response_class=HTMLResponse, summary="Create a manual asset")
async def new_asset_page(request: Request):
    user, company, _company_id, redirect = await _asset_write_context(request)
    if redirect:
        return redirect
    return await _main()._render_template(
        "assets/form.html", request, user,
        extra={"title": "Create asset", "company": company, "asset": None,
               **await _asset_type_picker(_company_id),
               "custom_fields": await asset_custom_fields_repo.list_field_definitions()},
    )


def _asset_type_mode() -> str:
    mode = str(getattr(_main().settings, "asset_type_mode", "auto") or "auto").lower()
    return mode if mode in asset_types.MODES else "auto"


async def _asset_type_picker(company_id: int) -> dict[str, Any]:
    """Template context for the asset type picker under ``ASSET_TYPE_MODE``."""
    mode = _asset_type_mode()
    return {
        "asset_type_mode": mode,
        "asset_type_groups": [] if mode == "manual" else asset_types.grouped(),
        "custom_asset_types": [] if mode == "auto" else await asset_repo.list_custom_asset_types(company_id),
    }


async def _manual_asset_values(
    form: Any, company_id: int, current: Mapping[str, Any] | None = None,
) -> dict[str, str | None]:
    name = str(form.get("name") or "").strip()
    if not name or len(name) > 255:
        raise HTTPException(status_code=422, detail="Asset name is required and must be 255 characters or fewer")
    values: dict[str, str | None] = {"name": name}
    for key in ("type", "status", "serial_number", "location"):
        value = str(form.get(key) or "").strip()
        if len(value) > 255:
            raise HTTPException(status_code=422, detail=f"{key.replace('_', ' ').title()} is too long")
        values[key] = value or None
    mode = _asset_type_mode()
    submitted = str(form.get("asset_type") or "").strip()
    typed = str(form.get("asset_type_name") or "").strip()
    if current and not typed and submitted.lower() == asset_types.effective(current):
        # Keep the asset's current type, even one the active mode no longer
        # offers (a custom type in auto mode, a catalogue type in manual mode).
        values["asset_type"] = asset_types.effective(current)
        values["type"] = asset_types.display_label(current)
        return values
    if mode != "auto" and (typed or not submitted):
        # Custom and manual modes take a typed-in name, reusing the spelling
        # of a matching type already entered for this company.
        try:
            key, label = asset_types.resolve_name(
                typed or values["type"], mode=mode,
                existing=await asset_repo.list_custom_asset_types(company_id),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        values["asset_type"] = key
        values["type"] = label
        return values
    if mode == "manual":
        raise HTTPException(status_code=422, detail="Enter an asset type")
    # Otherwise the type comes from the fixed catalogue. A bare free-text
    # ``type`` (older API clients) is mapped onto the closest catalogue entry.
    try:
        key = asset_types.normalise(submitted) if submitted else asset_types.derive(values["type"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Choose an asset type from the list") from exc
    values["asset_type"] = key
    values["type"] = asset_types.get(key).label
    return values


async def _save_submitted_custom_fields(asset_id: int, form: Any) -> None:
    for definition in await asset_custom_fields_repo.list_field_definitions():
        key = f"custom_{definition['id']}"
        raw = form.get(key)
        field_type = definition.get("field_type")
        if field_type == "checkbox":
            await asset_custom_fields_repo.set_asset_field_value(
                asset_id, int(definition["id"]), value_boolean=str(raw or "") == "1"
            )
        elif field_type == "date":
            value = str(raw or "").strip() or None
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail="Invalid custom field date") from exc
            await asset_custom_fields_repo.set_asset_field_value(
                asset_id, int(definition["id"]), value_date=value
            )
        else:
            value = str(raw or "").strip() or None
            await asset_custom_fields_repo.set_asset_field_value(
                asset_id, int(definition["id"]), value_text=value
            )


@router.post("/assets", status_code=303, summary="Create a manual asset")
async def create_manual_asset(request: Request):
    user, _company, company_id, redirect = await _asset_write_context(request)
    if redirect:
        return redirect
    form = await request.form()
    values = await _manual_asset_values(form, company_id)
    asset_id = await asset_repo.create_manual_asset(
        company_id=company_id, created_by=int(user["id"]), **values
    )
    await _save_submitted_custom_fields(asset_id, form)
    await audit_service.record(
        action="asset.manual.create", request=request, user_id=int(user["id"]),
        entity_type="asset", entity_id=asset_id,
        after={"company_id": company_id, "name": values["name"], "provenance": "manual"},
    )
    return RedirectResponse(url=f"/assets/{asset_id}", status_code=303)


def _require_expirations_available() -> None:
    """Expirations are linked to the Websites feature pack; hide them with it."""
    if not _main()._feature_pack_available("websites"):
        raise HTTPException(status_code=404, detail="Not Found")


def _expiration_key(item: dict[str, Any]) -> tuple[str, int, str]:
    return str(item["source_type"]), int(item["source_id"]), str(item["source_field"])


@router.get("/expirations", response_class=HTMLResponse)
async def expirations_page(request: Request):
    """Aggregate dates live from sources the current user may access."""
    _require_expirations_available()
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_asset_context(request, "menu.expirations")
    if redirect:
        return redirect
    global_view = bool(user.get("is_super_admin")) and request.query_params.get("scope") == "global"
    selected_company_id = None if global_view else company_id
    items = await expiration_repo.list_asset_dates(selected_company_id)
    if not global_view:
        for website in await websites_repo.list_websites(company_id):
            for field, checked_field, source_field, label in (
                ("certificate_expires_at", "certificate_checked_at", "certificate_expiry_source", "TLS certificate"),
                ("domain_expires_at", "domain_checked_at", "domain_expiry_source", "Domain registration"),
            ):
                if website.get(field):
                    items.append({
                        "source_type": "website", "source_id": int(website["id"]),
                        "source_field": field, "title": website.get("name") or "Website",
                        "detail": f"{label} · source {website.get(source_field) or 'unknown'} · checked {website.get(checked_field) or 'unknown'}",
                        "due_at": website[field], "company_id": company_id,
                        "company_name": company.get("name") if company else "Company",
                        "url": f"/websites/{website['id']}",
                    })
    access = await knowledge_base_service.build_access_context(user)
    articles = await knowledge_base_service.list_articles_for_context(
        access, include_unpublished=bool(user.get("is_super_admin"))
    )
    for article in articles:
        due = article.get("review_due_at") or article.get("review_due_at_utc")
        if not due:
            continue
        company_ids = [int(value) for value in article.get("company_ids", [])]
        if not global_view and company_ids and company_id not in company_ids:
            continue
        items.append({
            "source_type": "kb_review", "source_id": int(article["id"]),
            "source_field": "review_due_at", "title": article.get("title") or article.get("slug"),
            "detail": "Knowledge base review", "due_at": due,
            "company_id": company_ids[0] if len(company_ids) == 1 else company_id,
            "company_name": company.get("name") if not global_view else ("Global" if not company_ids else "Company restricted"),
            "url": f"/knowledge-base/articles/{article['slug']}",
        })
    metadata = {_expiration_key(row): row for row in await expiration_repo.list_metadata(selected_company_id)}
    today = datetime.now(timezone.utc).date()
    for item in items:
        due = item.get("due_at")
        due = due.date() if isinstance(due, datetime) else due
        if isinstance(due, str):
            try:
                due = date.fromisoformat(due[:10])
            except ValueError:
                due = None
        item["due_iso"] = due.isoformat() if isinstance(due, date) else ""
        item["days_remaining"] = (due - today).days if isinstance(due, date) else None
        meta = metadata.get(_expiration_key(item), {})
        item["lead_days"] = int(meta.get("lead_days") or 30)
        item["owner_user_id"] = meta.get("owner_user_id")
        item["owner_name"] = meta.get("owner_name")
        remaining = item["days_remaining"]
        item["state"] = "overdue" if remaining is not None and remaining < 0 else (
            "due_soon" if remaining is not None and remaining <= item["lead_days"] else "upcoming"
        )
        item.setdefault("url", f"/assets/{item['source_id']}")
    items.sort(key=lambda item: item.get("due_iso") or "9999-12-31")
    can_manage = bool(user.get("is_super_admin")) or main_module._membership_menu_can(
        user, membership, "menu.expirations", write=True
    )
    return await main_module._render_template("assets/expirations.html", request, user, extra={
        "title": "Expirations", "expirations": items, "global_view": global_view,
        "can_manage_expirations": can_manage,
        "expiration_owners": await users_repo.list_users_for_company(company_id),
    })


@router.post("/expirations/settings")
async def save_expiration_settings(request: Request):
    _require_expirations_available()
    main_module = _main()
    user, membership, _company, company_id, redirect = await _load_asset_context(request, "menu.expirations")
    if redirect:
        return redirect
    if not (user.get("is_super_admin") or main_module._membership_menu_can(user, membership, "menu.expirations", write=True)):
        raise HTTPException(status_code=403, detail="Write access to assets is required")
    form = await request.form()
    try:
        source_id = int(str(form.get("source_id")))
        lead_days = int(str(form.get("lead_days")))
        owner_user_id = int(str(form.get("owner_user_id"))) if form.get("owner_user_id") else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid expiration settings") from exc
    if not 0 <= lead_days <= 3650:
        raise HTTPException(status_code=422, detail="Lead time must be between 0 and 3650 days")
    source_type = str(form.get("source_type") or "")
    source_field = str(form.get("source_field") or "")
    if source_type not in {"warranty", "asset_custom_date", "kb_review"} or not source_field:
        raise HTTPException(status_code=422, detail="Invalid expiration source")
    if owner_user_id and owner_user_id not in {int(row["id"]) for row in await users_repo.list_users_for_company(company_id)}:
        raise HTTPException(status_code=422, detail="Owner must belong to this company")
    await expiration_repo.save_metadata(company_id=company_id,
        source_type=source_type, source_id=source_id,
        source_field=source_field, lead_days=lead_days,
        owner_user_id=owner_user_id)
    return main_module.flash_redirect("/expirations", "Expiration settings saved.", "success")


@router.post("/expirations/remind")
async def send_expiration_reminder(request: Request):
    _require_expirations_available()
    main_module = _main()
    user, membership, _company, company_id, redirect = await _load_asset_context(request, "menu.expirations")
    if redirect:
        return redirect
    can_write = bool(user.get("is_super_admin")) or main_module._membership_menu_can(
        user, membership, "menu.expirations", write=True
    )
    if not can_write:
        raise HTTPException(status_code=403, detail="Write access to assets is required")
    form = await request.form()
    source_type, source_field = str(form.get("source_type") or ""), str(form.get("source_field") or "")
    try:
        source_id = int(str(form.get("source_id")))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid expiration source") from exc
    visible = await expiration_repo.list_asset_dates(company_id)
    allowed = any(_expiration_key(row) == (source_type, source_id, source_field) for row in visible)
    if source_type == "kb_review":
        access = await knowledge_base_service.build_access_context(user)
        allowed = any(int(row["id"]) == source_id and row.get("review_due_at") for row in
                      await knowledge_base_service.list_articles_for_context(access, include_unpublished=bool(user.get("is_super_admin"))))
    if not allowed:
        raise HTTPException(status_code=404, detail="Expiration not found")
    try:
        result = await automations_service.handle_event("expirations.reminder", {
            "expiration": {"source_type": source_type, "source_id": source_id,
                           "source_field": source_field, "company_id": company_id},
            "actor": {"id": user.get("id")},
        })
        await expiration_repo.record_attempt(company_id=company_id, source_type=source_type,
            source_id=source_id, source_field=source_field, status="queued")
    except Exception as exc:
        await expiration_repo.record_attempt(company_id=company_id, source_type=source_type,
            source_id=source_id, source_field=source_field, status="failed", error=str(exc))
        return main_module.flash_redirect("/expirations", "Reminder failed and can be retried.", "error")
    message = "Reminder queued." if result else "No matching automation is enabled."
    return main_module.flash_redirect("/expirations", message, "success" if result else "warning")


@router.get("/assets/settings", response_class=HTMLResponse)
async def assets_settings_page(request: Request):
    main_module = _main()
    user, _membership, _, _, redirect = await _load_asset_context(request)
    if redirect:
        return redirect

    if not user.get("is_super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin privileges required",
        )

    extra = {
        "title": "Asset Custom Fields Settings",
        "is_super_admin": True,
        "required_field_rules": await asset_repo.list_required_field_rules(),
        "field_definitions": await asset_custom_fields_repo.list_field_definitions(),
    }
    return await main_module._render_template(
        "assets/settings.html", request, user, extra=extra
    )


@router.post("/assets/settings/required-fields")
async def save_asset_required_fields(request: Request):
    main_module = _main()
    user, _membership, _, _, redirect = await _load_asset_context(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin privileges required")
    form = await request.form()
    asset_type = str(form.get("asset_type") or "").strip()
    if not asset_type:
        raise HTTPException(status_code=422, detail="Asset type is required")
    allowed = {"owner", "support_contact", "criticality", "location", "operational_notes"}
    allowed.update(
        f"custom:{definition['id']}"
        for definition in await asset_custom_fields_repo.list_field_definitions()
    )
    keys = [str(value) for value in form.getlist("field_keys") if str(value) in allowed]
    await asset_repo.replace_required_fields(asset_type, keys)
    await audit_service.record(
        action="asset.requirements.update", request=request,
        entity_type="asset_type", after={"asset_type": asset_type, "field_keys": keys},
    )
    return main_module.flash_redirect("/assets/settings", "Required fields saved.", "success")


@router.get("/devices", response_class=HTMLResponse)
async def network_devices_page(request: Request):
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_asset_context(request, "menu.network_devices")
    if redirect:
        return redirect
    can_configure = bool(user.get("is_super_admin")) or main_module._membership_menu_can(user, membership, "menu.network_devices", write=True)
    can_import_ipam = bool(user.get("is_super_admin")) or main_module._membership_menu_can(
        user, membership, "menu.ipam", write=True
    )
    scanner_assets = await network_devices_repo.list_scanners(company_id) if can_configure else []
    enabled_scanners = [
        scanner for scanner in scanner_assets if scanner.get("network_scanner_enabled")
    ]
    available_scanners = [
        scanner
        for scanner in scanner_assets
        if not scanner.get("network_scanner_enabled")
    ]
    return await main_module._render_template(
        "devices/index.html",
        request,
        user,
        extra={
            "title": "Network Devices",
            "company": company,
            "devices": await network_devices_repo.list_for_company(company_id),
            "device_types": await network_devices_repo.list_device_types(),
            "scanners": enabled_scanners,
            "available_scanners": available_scanners,
            "can_manage_device_types": bool(user.get("is_super_admin")),
            "can_configure": can_configure,
            "can_import_ipam": can_import_ipam,
            "can_sync_hudu": bool(company.get("hudu_id")),
        },
    )


async def _device_ipam_context(request: Request):
    """Require device visibility and IPAM write access for imports."""
    main_module = _main()
    user, membership, _company, company_id, redirect = await _load_asset_context(
        request, "menu.network_devices"
    )
    if redirect:
        return main_module, user, company_id, redirect
    if not (user.get("is_super_admin") or main_module._membership_menu_can(
        user, membership, "menu.ipam", write=True
    )):
        raise HTTPException(status_code=403, detail="IPAM write access required")
    return main_module, user, company_id, None


def _selected_device_ids(form: Any) -> list[int]:
    try:
        values = list(dict.fromkeys(int(value) for value in form.getlist("device_ids")))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid device selection") from exc
    if not values or len(values) > 500:
        raise HTTPException(status_code=422, detail="Select between 1 and 500 devices")
    return values


@router.post("/devices/ipam-preview", summary="Preview discovered device IPAM imports")
async def preview_devices_in_ipam(request: Request):
    _main_module, _user, company_id, redirect = await _device_ipam_context(request)
    if redirect:
        return redirect
    form = await request.form()
    device_ids = _selected_device_ids(form)
    devices = await network_devices_repo.get_many_for_company(device_ids, company_id)
    if len(devices) != len(device_ids):
        raise HTTPException(status_code=404, detail="One or more discovered devices were not found")
    previews = await infrastructure_repo.preview_discovered_addresses(
        company_id, devices, use_wan=form.get("use_wan") == "1"
    )
    return JSONResponse({"items": [{
        "device_id": item["device"]["id"],
        "device": item["device"].get("hostname") or item["device"].get("mac_address") or f"Device {item['device']['id']}",
        "candidate": item["candidate"],
        "network": (f"{item['network']['name']} — {item['network']['cidr']}" if item["network"] else None),
        "asset": item["device"].get("matched_asset_name"),
        "asset_url": item["asset_url"],
        "source_url": item["source_url"],
        "reason": item["reason"],
    } for item in previews]})


@router.post("/devices/ipam-import", summary="Import discovered device addresses into IPAM")
async def import_devices_to_ipam(request: Request):
    main_module, _user, company_id, redirect = await _device_ipam_context(request)
    if redirect:
        return redirect
    form = await request.form()
    device_ids = _selected_device_ids(form)
    devices = await network_devices_repo.get_many_for_company(device_ids, company_id)
    if len(devices) != len(device_ids):
        raise HTTPException(status_code=404, detail="One or more discovered devices were not found")
    use_wan = form.get("use_wan") == "1"
    results = []
    for device in devices:
        result = await infrastructure_repo.import_discovered_address(
            company_id, device, use_wan=use_wan
        )
        results.append(result)
        await audit_service.record(
            action=f"infrastructure.address.discovery_{result['status']}", request=request,
            entity_type="ip_address", entity_id=result.get("record_id"),
            after={"company_id": company_id, "network_device_id": device["id"],
                   "candidate": result["candidate"], "reason": result.get("reason")},
        )
    counts = {key: sum(item["status"] == key for item in results)
              for key in ("created", "updated", "skipped", "conflict")}
    await audit_service.record(
        action="infrastructure.address.discovery_bulk", request=request,
        entity_type="ip_address", after={"company_id": company_id, **counts},
    )
    message = ", ".join(f"{value} {key}" for key, value in counts.items())
    level = "success" if counts["created"] or counts["updated"] else "info"
    return main_module.flash_redirect("/devices", f"IPAM import: {message}.", level)


@router.get("/infrastructure", summary="Legacy infrastructure entry point")
async def infrastructure_page(request: Request):
    """Keep existing bookmarks working while directing technicians to IPAM."""
    _user, _membership, _company, _company_id, redirect = await _load_asset_context(
        request, "menu.network_devices"
    )
    if redirect:
        return redirect
    return RedirectResponse(url="/ipam", status_code=status.HTTP_303_SEE_OTHER)


async def _infrastructure_page_context(request: Request, permission_key: str):
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_asset_context(
        request, permission_key
    )
    if redirect:
        return main_module, user, redirect
    can_edit = bool(user.get("is_super_admin")) or main_module._membership_menu_can(
        user, membership, permission_key, write=True
    )
    data = await infrastructure_repo.overview(company_id)
    data.update({
        "company": company, "can_edit": can_edit,
        "assets": await asset_repo.list_company_assets(company_id),
        "address_states": sorted(infrastructure_repo.ADDRESS_STATES),
    })
    return main_module, user, data


@router.get("/ipam", response_class=HTMLResponse, summary="IP address management")
async def ipam_page(request: Request):
    main_module, user, data = await _infrastructure_page_context(request, "menu.ipam")
    if isinstance(data, RedirectResponse):
        return data
    data["title"] = "IP address management"
    return await main_module._render_template(
        "infrastructure/ipam.html", request, user, extra=data
    )


@router.get("/racks", response_class=HTMLResponse, summary="Rack management")
async def racks_page(request: Request):
    main_module, user, data = await _infrastructure_page_context(request, "menu.racks")
    if isinstance(data, RedirectResponse):
        return data
    data["title"] = "Rack management"
    data["item_types"] = rack_item_types.ITEM_TYPES
    data["item_type_groups"] = rack_item_types.grouped()
    data["default_item_type"] = rack_item_types.DEFAULT_KEY
    data["asset_item_types"] = {
        asset["id"]: rack_item_types.for_asset(asset) for asset in data.get("assets") or [] if asset.get("id")
    }
    data["connectors"] = rack_item_types.CONNECTORS
    data["image_path"] = rack_item_types.image_path
    data["port_catalog"] = rack_dashboard.port_catalog(data.get("equipment") or [])
    data["item_type_labels"] = {item_type.key: item_type.label for item_type in rack_item_types.ITEM_TYPES}
    for item in data.get("equipment") or []:
        item["item_type"] = rack_item_types.get(item.get("item_type")).key
    data["workspace"] = rack_dashboard.build_workspace(
        data, request.query_params.get("rack"), request.query_params.get("view")
    )
    return await main_module._render_template(
        "infrastructure/racks.html", request, user, extra=data
    )


def _rack_location(form: Any, rack_id: Any = None, anchor: str = "") -> str:
    """Return the rack workspace URL to land on after a rack form submission.

    Only an integer rack id is echoed back, so the redirect can never leave
    the rack page.
    """
    try:
        selected = int(rack_id if rack_id is not None else form.get("rack_id"))
    except (TypeError, ValueError):
        return f"/racks{anchor}"
    return f"/racks?rack={selected}{anchor}"


def _rack_connections(form: Any, item_type: str) -> dict[str, Any]:
    """Parse connection counts and per-port links from a rack item form.

    Link fields are named ``port-<connector>-<ordinal>-asset`` (or ``-source``
    for a power supply's feeding outlet), ``-peer`` for a specific remote port
    (a network port, or the PSU an outlet feeds) and ``-label``.
    """
    catalogue = rack_item_types.get(item_type)
    counts: dict[str, int] = {}
    for connector in catalogue.connector_keys:
        raw = form.get(f"port_count_{connector}")
        if raw is None and connector == "data":
            raw = form.get("port_count")
        counts[connector] = int(raw or 0)
    links = []
    for connector, count in counts.items():
        target = "source" if connector == rack_item_types.POWER_INPUT else "asset"
        for ordinal in range(1, count + 1):
            prefix = f"port-{connector}-{ordinal}-"
            target_raw = form.get(prefix + target)
            label = _optional_text(form, prefix + "label")
            if target_raw is None and label is None:
                continue
            # The device picker holds an asset id, or "item:<id>" for a rack
            # item without an asset; the remote port is sent as "-peer".
            target_id = int(target_raw) if target_raw and not str(target_raw).startswith("item:") else None
            peer_raw = form.get(prefix + "peer") if connector in infrastructure_repo.PEER_TARGETS else None
            links.append(infrastructure_repo.PortLink(
                connector, ordinal,
                asset_id=target_id if target == "asset" else None, label=label,
                source_port_id=target_id if target == "source" else None,
                peer_port_id=int(peer_raw) if peer_raw else None))
    return {"port_counts": counts, "port_links": links}


def _optional_text(form: Any, key: str, limit: int = 191) -> str | None:
    value = str(form.get(key) or "").strip()
    if len(value) > limit:
        raise HTTPException(status_code=422, detail=f"Invalid {key.replace('_', ' ')}")
    return value or None


def _required_text(form: Any, key: str, limit: int = 191) -> str:
    value = str(form.get(key) or "").strip()
    if not value or len(value) > limit:
        raise HTTPException(status_code=422, detail=f"Invalid {key.replace('_', ' ')}")
    return value


async def _infrastructure_write_context(request: Request, permission_key: str):
    main_module = _main()
    user, membership, _company, company_id, redirect = await _load_asset_context(
        request, permission_key
    )
    if redirect:
        return user, company_id, redirect
    if not (user.get("is_super_admin") or main_module._membership_menu_can(
        user, membership, permission_key, write=True
    )):
        raise HTTPException(status_code=403, detail="Network documentation write access required")
    return user, company_id, None


@router.post("/api/infrastructure/networks", status_code=201, summary="Create a CIDR network")
async def create_ip_network(request: Request):
    user, company_id, redirect = await _infrastructure_write_context(request, "menu.ipam")
    if redirect:
        return redirect
    form = await request.form()
    try:
        record_id = await infrastructure_repo.create_network(
            company_id, _required_text(form, "name"), _required_text(form, "cidr", 49),
            str(form.get("description") or "").strip()[:1000] or None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit_service.record(action="infrastructure.network.create", request=request,
                               entity_type="ip_network", entity_id=record_id,
                               after={"company_id": company_id})
    return _main().flash_redirect("/ipam#networks", "Network added.", "success")


@router.post("/api/infrastructure/addresses", status_code=201, summary="Assign or reserve an IP address")
async def create_ip_address(request: Request):
    user, company_id, redirect = await _infrastructure_write_context(request, "menu.ipam")
    if redirect:
        return redirect
    form = await request.form()
    try:
        asset_id = int(form["asset_id"]) if form.get("asset_id") else None
        record_id = await infrastructure_repo.create_address(
            company_id, int(form.get("network_id")), _required_text(form, "address", 45),
            str(form.get("state") or "reserved"), asset_id,
            str(form.get("dns_names") or "").replace(",", "\n").splitlines(),
            str(form.get("notes") or "").strip()[:1000] or None)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit_service.record(action="infrastructure.address.create", request=request,
                               entity_type="ip_address", entity_id=record_id,
                               after={"company_id": company_id, "asset_id": asset_id})
    return _main().flash_redirect("/ipam#addresses", "IP address documented.", "success")


@router.post("/api/infrastructure/racks", status_code=201, summary="Create a rack")
async def create_rack(request: Request):
    _user, company_id, redirect = await _infrastructure_write_context(request, "menu.racks")
    if redirect:
        return redirect
    form = await request.form()
    try:
        record_id = await infrastructure_repo.create_rack(
            company_id, _required_text(form, "name"),
            str(form.get("location") or "").strip()[:255] or None,
            int(form.get("unit_count")), int(form.get("depth_mm")))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit_service.record(action="infrastructure.rack.create", request=request,
                               entity_type="rack", entity_id=record_id,
                               after={"company_id": company_id})
    return _main().flash_redirect(_rack_location(form, record_id), "Rack added.", "success")


@router.post("/api/infrastructure/racks/{rack_id}/resize", summary="Change a rack's height and depth")
async def resize_rack(request: Request, rack_id: int):
    _user, company_id, redirect = await _infrastructure_write_context(request, "menu.racks")
    if redirect:
        return redirect
    form = await request.form()
    try:
        unit_count, depth_mm = int(form.get("unit_count")), int(form.get("depth_mm"))
        before = await infrastructure_repo.resize_rack(company_id, rack_id, unit_count, depth_mm)
    except infrastructure_repo.RackResizeBlocked as exc:
        # Send the technician back to the rack with the items that are in the
        # way rather than a bare error page.
        return _main().flash_redirect(_rack_location(form, rack_id), str(exc), "error")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit_service.record(action="infrastructure.rack.resize", request=request,
                               entity_type="rack", entity_id=rack_id,
                               before={"company_id": company_id, **before},
                               after={"company_id": company_id, "unit_count": unit_count,
                                      "depth_mm": depth_mm})
    return _main().flash_redirect(_rack_location(form, rack_id), "Rack size updated.", "success")


async def _rack_image_selection(form: Any, company_id: int, item_type: str) -> list[int] | None:
    raw = form.get("image_ids")
    if raw is None:
        return None  # Older clients leave existing attachments untouched.
    try:
        image_ids = json.loads(str(raw))
    except (ValueError, TypeError):
        raise ValueError("Invalid image selection") from None
    if (not isinstance(image_ids, list) or len(image_ids) > 100
            or any(type(value) is not int or value <= 0 for value in image_ids)):
        raise ValueError("Invalid image selection")
    image_ids = list(dict.fromkeys(image_ids))
    await rack_image_repo.validate_selection(company_id, item_type, image_ids)
    return image_ids


@router.post("/api/infrastructure/rack-equipment", status_code=201, summary="Place equipment in a rack",
             description="Optional image_ids form field contains a JSON list of company library image IDs "
                         "to attach to the newly placed rack item.")
async def place_rack_asset(request: Request):
    _user, company_id, redirect = await _infrastructure_write_context(request, "menu.racks")
    if redirect:
        return redirect
    form = await request.form()
    try:
        asset_id = int(form.get("asset_id")) if form.get("asset_id") else None
        item_type = str(form.get("item_type") or rack_item_types.DEFAULT_KEY)
        image_ids = await _rack_image_selection(form, company_id, item_type)
        connections = _rack_connections(form, item_type)
        record_id = await infrastructure_repo.place_asset(
            company_id, int(form.get("rack_id")), asset_id, int(form.get("start_unit")),
            int(form.get("unit_height")), str(form.get("face") or "front"),
            str(form.get("notes") or "").strip()[:1000] or None,
            int(form.get("width_lanes") or 3), int(form.get("start_lane") or 1),
            str(form.get("depth_mode") or "half"),
            int(form.get("power_draw_watts")) if form.get("power_draw_watts") else None,
            item_type, _optional_text(form, "name"),
            int(form.get("port_count") or 0), **connections)
        if image_ids is not None:
            await rack_image_repo.save_selection(company_id, record_id, image_ids)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit_service.record(action="infrastructure.rack_equipment.create", request=request,
                               entity_type="rack_equipment", entity_id=record_id,
                               after={"company_id": company_id, "asset_id": asset_id})
    return _main().flash_redirect(_rack_location(form), "Rack item placed.", "success")


@router.post("/api/infrastructure/rack-equipment/{equipment_id}/ports/{port_number}", summary="Link a rack item port to an asset")
async def link_rack_item_port(request: Request, equipment_id: int, port_number: int):
    _user, company_id, redirect = await _infrastructure_write_context(request, "menu.racks")
    if redirect:
        return redirect
    form = await request.form()
    try:
        asset_id = int(form.get("asset_id")) if form.get("asset_id") else None
        await infrastructure_repo.link_equipment_port(
            company_id, equipment_id, port_number, asset_id, _optional_text(form, "label"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit_service.record(action="infrastructure.rack_port.link", request=request,
                               entity_type="rack_equipment_port", entity_id=equipment_id,
                               after={"company_id": company_id, "port_number": port_number,
                                      "asset_id": asset_id})
    return _main().flash_redirect(_rack_location(form), "Port link updated.", "success")


@router.post("/api/infrastructure/rack-equipment/{equipment_id}/edit", summary="Edit a rack item",
             description="Optional image_ids form field contains a JSON list of company library image IDs. "
                         "Replaces the item's attachments when provided; preserves them when omitted.")
async def edit_rack_equipment(request: Request, equipment_id: int):
    _user, company_id, redirect = await _infrastructure_write_context(request, "menu.racks")
    if redirect:
        return redirect
    form = await request.form()
    try:
        asset_id = int(form.get("asset_id")) if form.get("asset_id") else None
        item_type = str(form.get("item_type") or rack_item_types.DEFAULT_KEY)
        image_ids = await _rack_image_selection(form, company_id, item_type)
        extra: dict[str, Any] = {}
        if form.get("start_unit"):
            # The full edit form sends the same position and connection
            # fields as placement; older clients send only the basics.
            extra = _rack_connections(form, item_type)
            extra["position"] = infrastructure_repo.RackPosition(
                int(form.get("start_unit")), int(form.get("unit_height") or 1),
                str(form.get("face") or "front"), int(form.get("width_lanes") or 3),
                int(form.get("start_lane") or 1), str(form.get("depth_mode") or "half"))
        await infrastructure_repo.update_rack_equipment(
            company_id, equipment_id, _optional_text(form, "name"), item_type, asset_id,
            int(form.get("power_draw_watts")) if form.get("power_draw_watts") else None,
            str(form.get("notes") or "").strip()[:1000] or None, **extra)
        if image_ids is not None:
            await rack_image_repo.save_selection(company_id, equipment_id, image_ids)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit_service.record(action="infrastructure.rack_equipment.update", request=request,
                               entity_type="rack_equipment", entity_id=equipment_id,
                               after={"company_id": company_id, "asset_id": asset_id})
    return _main().flash_redirect(_rack_location(form), "Rack item updated.", "success")


@router.post("/api/infrastructure/rack-reservations/{reservation_id}/edit", summary="Edit a rack reservation")
async def edit_rack_reservation(request: Request, reservation_id: int):
    _user, company_id, redirect = await _infrastructure_write_context(request, "menu.racks")
    if redirect:
        return redirect
    form = await request.form()
    try:
        await infrastructure_repo.update_rack_reservation(
            company_id, reservation_id,
            str(form.get("label") or "").strip()[:191] or None,
            str(form.get("owner") or "").strip()[:191] or None,
            str(form.get("notes") or "").strip()[:1000] or None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit_service.record(action="infrastructure.rack_reservation.update", request=request,
                               entity_type="rack_reservation", entity_id=reservation_id,
                               after={"company_id": company_id})
    return _main().flash_redirect(_rack_location(form), "Reservation updated.", "success")


@router.post("/api/infrastructure/rack-reservations", status_code=201, summary="Reserve rack space")
async def reserve_rack_space(request: Request):
    _user, company_id, redirect = await _infrastructure_write_context(request, "menu.racks")
    if redirect:
        return redirect
    form = await request.form()
    try:
        record_id = await infrastructure_repo.reserve_space(
            company_id, int(form.get("rack_id")), int(form.get("start_unit")),
            int(form.get("unit_height")), str(form.get("face") or "front"),
            int(form.get("width_lanes") or 3), int(form.get("start_lane") or 1),
            str(form.get("depth_mode") or "half"),
            str(form.get("label") or "").strip()[:191] or None,
            str(form.get("owner") or "").strip()[:191] or None,
            str(form.get("notes") or "").strip()[:1000] or None)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit_service.record(action="infrastructure.rack_reservation.create", request=request,
                               entity_type="rack_reservation", entity_id=record_id,
                               after={"company_id": company_id})
    return _main().flash_redirect(_rack_location(form), "Rack space reserved.", "success")


@router.post("/api/infrastructure/{record_type}/{record_id}/delete", summary="Delete infrastructure documentation")
async def delete_infrastructure_record(request: Request, record_type: str, record_id: int):
    tables = {"networks": "ip_networks", "addresses": "ip_addresses",
              "racks": "racks", "rack-equipment": "rack_equipment",
              "rack-reservations": "rack_reservations"}
    if record_type not in tables:
        raise HTTPException(status_code=404, detail="Record type not found")
    permission_key = "menu.ipam" if record_type in {"networks", "addresses"} else "menu.racks"
    _user, company_id, redirect = await _infrastructure_write_context(request, permission_key)
    if redirect:
        return redirect
    await infrastructure_repo.delete_record(tables[record_type], record_id, company_id)
    await audit_service.record(action="infrastructure.record.delete", request=request,
                               entity_type=record_type, entity_id=record_id,
                               before={"company_id": company_id})
    if record_type in {"networks", "addresses"}:
        destination = "/ipam"
    elif record_type == "racks":
        destination = "/racks"
    else:
        destination = _rack_location(await request.form())
    return _main().flash_redirect(destination, "Documentation removed.", "success")


@router.post("/devices/discovered/{device_id}/hudu-sync")
async def sync_network_device_to_hudu(request: Request, device_id: int):
    """Send a discovered device to Hudu or synchronize its managed fields."""
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_asset_context(request, "menu.network_devices")
    if redirect:
        return redirect
    if not (
        user.get("is_super_admin")
        or main_module._membership_menu_can(user, membership, "menu.network_devices", write=True)
    ):
        raise HTTPException(status_code=403, detail="Network Devices write access required")
    hudu_company_id = str(company.get("hudu_id") or "").strip()
    if not hudu_company_id:
        return main_module.flash_redirect(
            "/devices", "Link this company to Hudu before syncing devices.", "error"
        )
    device = await network_devices_repo.get_for_company(device_id, company_id)
    if not device:
        raise HTTPException(status_code=404, detail="Discovered device not found")
    try:
        result = await hudu_service.sync_discovered_device(
            company_id=hudu_company_id, device=device
        )
    except (
        hudu_service.HuduConfigurationError,
        hudu_service.HuduDeviceSyncError,
    ) as exc:
        return main_module.flash_redirect("/devices", str(exc), "error")
    except Exception as exc:
        log_info("Hudu device sync failed", device_id=device_id, error=str(exc))
        return main_module.flash_redirect(
            "/devices",
            "Hudu could not sync this device. Check the integration and try again.",
            "error",
        )
    verb = "Sent" if result["action"] == "created" else "Synced"
    return main_module.flash_redirect(
        "/devices", f"{verb} device with Hudu.", "success"
    )


@router.post("/devices/discovered/{device_id}")
async def update_network_device(request: Request, device_id: int):
    main_module = _main()
    user, membership, _company, company_id, redirect = await _load_asset_context(
        request, "menu.network_devices"
    )
    if redirect:
        return redirect
    if not (
        user.get("is_super_admin")
        or main_module._membership_menu_can(user, membership, "menu.network_devices", write=True)
    ):
        raise HTTPException(status_code=403, detail="Network Devices write access required")

    form = await request.form()
    state_value = str(form.get("state") or "").title()
    if state_value not in {"New", "Known", "Unknown"}:
        raise HTTPException(status_code=422, detail="Invalid device state")
    raw_type = form.get("device_type_id")
    try:
        device_type_id = int(raw_type) if raw_type else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid device type") from exc
    valid_type_ids = {
        int(item["id"]) for item in await network_devices_repo.list_device_types()
    }
    if device_type_id is not None and device_type_id not in valid_type_ids:
        raise HTTPException(status_code=422, detail="Invalid device type")
    description = str(form.get("description") or "").strip() or None
    if description and len(description) > 2000:
        raise HTTPException(status_code=422, detail="Description is too long")
    agent_not_required = form.get("agent_not_required") == "1"
    await network_devices_repo.update_device(
        device_id,
        company_id,
        state_value,
        device_type_id,
        description,
        agent_not_required,
    )
    return RedirectResponse(url="/devices", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/devices/discovered-bulk-update")
async def bulk_update_network_devices(request: Request):
    main_module = _main()
    user, membership, _company, company_id, redirect = await _load_asset_context(
        request, "menu.network_devices"
    )
    if redirect:
        return redirect
    if not (
        user.get("is_super_admin")
        or main_module._membership_menu_can(user, membership, "menu.network_devices", write=True)
    ):
        raise HTTPException(status_code=403, detail="Network Devices write access required")

    form = await request.form()
    raw_ids = form.getlist("device_ids")
    try:
        device_ids = list(dict.fromkeys(int(value) for value in raw_ids))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid device selection") from exc
    if not device_ids or len(device_ids) > 500:
        raise HTTPException(status_code=422, detail="Select between 1 and 500 devices")

    action = str(form.get("bulk_action") or "")
    updates: dict[str, object] = {}
    if action == "state":
        state_value = str(form.get("state") or "").title()
        if state_value not in {"New", "Known", "Unknown"}:
            raise HTTPException(status_code=422, detail="Invalid device state")
        updates["state"] = state_value
    elif action == "device_type":
        raw_type = form.get("device_type_id")
        try:
            device_type_id = int(raw_type) if raw_type else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid device type") from exc
        valid_type_ids = {
            int(item["id"]) for item in await network_devices_repo.list_device_types()
        }
        if device_type_id is not None and device_type_id not in valid_type_ids:
            raise HTTPException(status_code=422, detail="Invalid device type")
        updates.update(device_type_id=device_type_id, clear_device_type=True)
    elif action == "description":
        description = str(form.get("description") or "").strip() or None
        if description and len(description) > 2000:
            raise HTTPException(status_code=422, detail="Description is too long")
        updates.update(description=description, update_description=True)
    elif action == "agent_not_required":
        value = str(form.get("agent_not_required") or "")
        if value not in {"0", "1"}:
            raise HTTPException(status_code=422, detail="Invalid agent requirement")
        updates["agent_not_required"] = value == "1"
    else:
        raise HTTPException(status_code=422, detail="Select a bulk action")

    await network_devices_repo.bulk_update_devices(device_ids, company_id, **updates)
    return main_module.flash_redirect(
        "/devices",
        f"Updated {len(device_ids)} discovered device{'s' if len(device_ids) != 1 else ''}.",
        "success",
    )


@router.post("/devices/discovered-purge")
async def purge_network_devices(request: Request):
    """Remove discoveries outside the boundaries of their originating scanner."""
    main_module = _main()
    user, membership, _company, company_id, redirect = await _load_asset_context(
        request, "menu.network_devices"
    )
    if redirect:
        return redirect
    if not (
        user.get("is_super_admin")
        or main_module._membership_menu_can(user, membership, "menu.network_devices", write=True)
    ):
        raise HTTPException(status_code=403, detail="Network Devices write access required")

    purged = await network_devices_repo.purge_out_of_scope(company_id)
    message = (
        f"Purged {purged} out-of-scope discovered device{'s' if purged != 1 else ''}."
        if purged
        else "No out-of-scope discovered devices were purged."
    )
    return main_module.flash_redirect(
        "/devices", message, "success" if purged else "info"
    )


@router.post("/devices/device-types")
async def add_device_type_from_devices(request: Request):
    user, _membership, _company, _company_id, redirect = await _load_asset_context(
        request, "menu.network_devices"
    )
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin privileges required")
    form = await request.form()
    name = str(form.get("name") or "").strip()
    if not name or len(name) > 100:
        raise HTTPException(status_code=422, detail="Enter a device type name")
    vendors = _parse_mac_vendors(str(form.get("mac_vendors") or ""))
    await network_devices_repo.create_device_type(
        name, vendors, form.get("auto_assign") == "1"
    )
    return RedirectResponse(url="/devices", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/devices/device-types/{device_type_id}")
async def update_device_type_from_devices(request: Request, device_type_id: int):
    user, _membership, _company, _company_id, redirect = await _load_asset_context(
        request, "menu.network_devices"
    )
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin privileges required")
    form = await request.form()
    name = str(form.get("name") or "").strip()
    if not name or len(name) > 100:
        raise HTTPException(status_code=422, detail="Enter a device type name")
    await network_devices_repo.update_device_type(
        device_type_id,
        name,
        _parse_mac_vendors(str(form.get("mac_vendors") or "")),
        form.get("auto_assign") == "1",
    )
    return RedirectResponse(url="/devices", status_code=status.HTTP_303_SEE_OTHER)


def _parse_mac_vendors(value: str) -> list[str]:
    """Accept one vendor per line or comma-separated, preserving display casing."""
    vendors: dict[str, str] = {}
    for item in value.replace(",", "\n").splitlines():
        vendor = " ".join(item.split())
        if vendor:
            vendors.setdefault(vendor.casefold(), vendor[:255])
    return list(vendors.values())


@router.post("/devices/device-types/{device_type_id}/delete")
async def delete_device_type_from_devices(request: Request, device_type_id: int):
    user, _membership, _company, _company_id, redirect = await _load_asset_context(
        request, "menu.network_devices"
    )
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin privileges required")
    await network_devices_repo.delete_device_type(device_type_id)
    return RedirectResponse(url="/devices", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/devices/alerts")
async def configure_network_device_alerts(request: Request):
    main_module = _main()
    user, membership, _company, company_id, redirect = await _load_asset_context(
        request, "menu.network_devices"
    )
    if redirect:
        return redirect
    if not (
        user.get("is_super_admin")
        or main_module._membership_menu_can(user, membership, "menu.network_devices", write=True)
    ):
        raise HTTPException(status_code=403, detail="Network Devices write access required")
    form = await request.form()
    await company_repo.update_company(
        company_id,
        network_device_ticket_alerts_enabled=(
            1 if form.get("network_device_ticket_alerts_enabled") == "1" else 0
        ),
    )
    return RedirectResponse(url="/devices", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/assets/settings/device-types")
async def add_network_device_type(request: Request):
    user, _membership, _company, _company_id, redirect = await _load_asset_context(
        request
    )
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin privileges required")
    form = await request.form()
    name = str(form.get("name") or "").strip()
    if not name or len(name) > 100:
        raise HTTPException(status_code=422, detail="Enter a device type name")
    await network_devices_repo.create_device_type(name)
    return RedirectResponse(
        url="/assets/settings", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/assets/settings/device-types/{device_type_id}/delete")
async def delete_network_device_type(request: Request, device_type_id: int):
    user, _membership, _company, _company_id, redirect = await _load_asset_context(
        request
    )
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin privileges required")
    await network_devices_repo.delete_device_type(device_type_id)
    return RedirectResponse(
        url="/assets/settings", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/devices/scanners")
async def add_network_scanner(request: Request):
    main_module = _main()
    user, membership, _company, company_id, redirect = await _load_asset_context(
        request, "menu.network_devices"
    )
    if redirect:
        return redirect
    if not (
        user.get("is_super_admin")
        or main_module._membership_menu_can(user, membership, "menu.network_devices", write=True)
    ):
        raise HTTPException(status_code=403, detail="Network Devices write access required")

    form = await request.form()
    try:
        device_id = int(form.get("device_id"))
        interval = max(5, min(10080, int(form.get("interval_minutes", 360))))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail="Select an asset and valid interval"
        )

    wan_cidrs, local_cidrs = _scanner_scope_from_form(form)
    await network_devices_repo.configure_scanner(
        device_id, company_id, True, interval, wan_cidrs, local_cidrs
    )
    return RedirectResponse(url="/devices", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/devices/scanners/{device_id}")
async def configure_network_scanner(request: Request, device_id: int):
    main_module = _main()
    user, membership, _company, company_id, redirect = await _load_asset_context(
        request, "menu.network_devices"
    )
    if redirect:
        return redirect
    if not (
        user.get("is_super_admin")
        or main_module._membership_menu_can(user, membership, "menu.network_devices", write=True)
    ):
        raise HTTPException(status_code=403, detail="Network Devices write access required")
    form = await request.form()
    try:
        interval = max(5, min(10080, int(form.get("interval_minutes", 360))))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Scan interval must be a number")
    wan_cidrs, local_cidrs = _scanner_scope_from_form(form)
    await network_devices_repo.configure_scanner(
        device_id,
        company_id,
        form.get("enabled") == "1",
        interval,
        wan_cidrs,
        local_cidrs,
    )
    return RedirectResponse(url="/devices", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/devices/scanners/{device_id}/scan")
async def scan_network_now(request: Request, device_id: int):
    main_module = _main()
    user, membership, _company, company_id, redirect = await _load_asset_context(
        request, "menu.network_devices"
    )
    if redirect:
        return redirect
    if not (
        user.get("is_super_admin")
        or main_module._membership_menu_can(user, membership, "menu.network_devices", write=True)
    ):
        raise HTTPException(status_code=403, detail="Network Devices write access required")

    scanner = next(
        (
            item
            for item in await network_devices_repo.list_scanners(company_id)
            if int(item["id"]) == device_id and item.get("network_scanner_enabled")
        ),
        None,
    )
    if scanner is None:
        raise HTTPException(status_code=404, detail="Enabled subnet scanner not found")

    payload = {"type": "scan_network"}
    delivered = await tray_service.send_to_device(
        str(scanner.get("device_uid") or ""), payload
    )
    await tray_repo.log_command(
        device_id=device_id,
        command="scan_network",
        payload_json=json.dumps(payload),
        initiated_by_user_id=int(user["id"]),
        status="delivered" if delivered else "queued",
    )
    message = (
        "Scan request sent to the agent."
        if delivered
        else "Scan request queued until the agent reconnects."
    )
    return main_module.flash_redirect("/devices", message, "success")


def _parse_external_references(raw: str) -> list[dict[str, str]]:
    """Parse one ``Label | https://…`` reference per line."""
    references: list[dict[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        label, separator, url = line.partition("|")
        if not separator:
            url, label = label, label
        label, url = label.strip(), url.strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=422, detail=f"Invalid external reference: {url}")
        references.append({"label": label[:255] or url, "url": url})
    return references


def _clean_optional(form: Any, key: str) -> str | None:
    value = str(form.get(key) or "").strip()
    return value or None


@router.get("/assets/{asset_id}", response_class=HTMLResponse)
async def asset_detail_page(
    request: Request, asset_id: int, customer_preview: bool = Query(False)
):
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_asset_context(request)
    if redirect:
        return redirect

    record = await asset_repo.get_asset_by_id(asset_id)
    record_company_id = record.get("company_id") if record else None
    is_super_admin = bool(user.get("is_super_admin"))
    can_write = is_super_admin or main_module._membership_menu_can(
        user, membership, "menu.assets", write=True
    )
    can_view_relationships = is_super_admin or main_module._membership_menu_can(
        user, membership, "menu.asset_relationships"
    )
    can_view_photos = is_super_admin or main_module._membership_menu_can(
        user, membership, "menu.asset_photos"
    )
    can_edit_relationships = is_super_admin or main_module._membership_menu_can(
        user, membership, "menu.asset_relationships", write=True
    )
    can_edit_photos = is_super_admin or main_module._membership_menu_can(
        user, membership, "menu.asset_photos", write=True
    )
    customer_safe = (not can_write) or customer_preview
    if (record_company_id is None or int(record_company_id) != company_id
            or (not is_super_admin and not can_write and (not bool(record.get("customer_visible")) or not await _customer_role_can_view_asset(membership, company_id, asset_id)))):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found"
        )

    if customer_safe:
        record = _customer_asset(dict(record))
    try:
        references = json.loads(record.get("external_references_json") or "[]")
    except (TypeError, ValueError):
        references = []
    if not isinstance(references, list):
        references = []
    definitions = [] if customer_safe else await asset_custom_fields_repo.list_field_definitions()
    values = {} if customer_safe else await asset_custom_fields_repo.get_all_asset_field_values([asset_id])
    custom_fields = [
        {**definition, "value": values.get(asset_id, {}).get(definition["id"])}
        for definition in definitions
    ]
    required_fields = [] if customer_safe else await asset_repo.list_required_fields(record.get("type"))
    required_missing = [
        key for key in required_fields
        if not record.get(key) and not any(
            str(field.get("id")) == key.removeprefix("custom:") and field.get("value")
            for field in custom_fields
        )
    ]
    access_context = await knowledge_base_service.build_access_context(user)
    visible_articles = await knowledge_base_service.list_articles_for_context(
        access_context, include_unpublished=bool(user.get("is_super_admin"))
    )
    articles_by_id = {int(article["id"]): article for article in visible_articles}
    linked_runbooks = [
        article for article in visible_articles
        if asset_id in {int(value) for value in article.get("asset_ids", [])}
    ]
    company_assets = await asset_repo.list_company_assets(company_id)
    if customer_safe:
        company_assets = [item for item in company_assets if bool(item.get("customer_visible")) and await _customer_role_can_view_asset(membership, company_id, int(item["id"]))]
    assets_by_id = {int(item["id"]): item for item in company_assets}
    relationship_targets: list[dict[str, Any]] = []
    target_records: dict[str, dict[int, dict[str, Any]]] = {
        "asset": assets_by_id, "knowledge_base_article": articles_by_id,
    }
    if not customer_safe:
        can_view_tickets = bool(user.get("is_super_admin")) or main_module._membership_menu_can(user, membership, "menu.tickets", write=True)
        can_view_infrastructure = bool(user.get("is_super_admin")) or main_module._membership_menu_can(user, membership, "menu.network_devices")
        tickets = await tickets_repo.list_tickets(company_id=company_id, limit=None) if can_view_tickets else []
        infrastructure = await infrastructure_repo.overview(company_id) if can_view_infrastructure else {"networks": [], "racks": []}
        target_records.update({
            "ticket": {int(item["id"]): item for item in tickets},
            "process_run": {int(item["id"]): item for item in await processes_repo.list_runs(company_id) if item.get("status") != "cancelled"}
                if main_module._membership_menu_can(user, membership, "menu.processes") else {},
            "website": {int(item["id"]): item for item in await websites_repo.list_websites(company_id)}
                if main_module._membership_menu_can(user, membership, "menu.websites") else {},
            "ip_network": {int(item["id"]): item for item in infrastructure["networks"]}
                if main_module._membership_menu_can(user, membership, "menu.ipam") else {},
            "rack": {int(item["id"]): item for item in infrastructure["racks"]}
                if main_module._membership_menu_can(user, membership, "menu.racks") else {},
        })
        for target_type, records in target_records.items():
            for target_id, target in records.items():
                if target_type == "asset" and (target_id == asset_id or target.get("archived_at")):
                    continue
                relationship_targets.append(_relationship_target(target_type, target))
        relationship_targets.sort(key=lambda item: (item["type"], item["label"].casefold()))
    relationships = []
    for relationship in (await asset_repo.list_relationships_for_asset(company_id, asset_id) if can_view_relationships else []):
        item = dict(relationship)
        if item["direction"] == "inbound":
            target = assets_by_id.get(int(item["source_asset_id"]))
        elif item["target_type"] in target_records:
            target = target_records[item["target_type"]].get(int(item["target_id"]))
        else:
            target = None
        # Silently omit inaccessible/deleted records: their existence must not leak.
        if not target:
            continue
        item["target"] = _relationship_target("asset" if item["direction"] == "inbound" else item["target_type"], target)
        relationships.append(item)
    can_view_bcp = bool(user.get("is_super_admin")) or main_module._membership_menu_can(
        user, membership, "menu.continuity"
    )
    bcp_context = (
        await bcp_repo.list_bcp_context_for_asset(company_id, asset_id)
        if can_view_bcp and not customer_safe else []
    )
    infrastructure_links = (
        await infrastructure_repo.for_asset(company_id, asset_id)
        if not customer_safe else {"addresses": [], "placements": []}
    )
    # Interfaces belong to the network map pack; the card disappears with it.
    can_view_network_map = (
        not customer_safe and main_module._feature_pack_available("network_map")
        and (is_super_admin or main_module._membership_menu_can(user, membership, "menu.network_map")))
    network_interfaces = None
    if can_view_network_map:
        from app.features.network_map import routes as network_map_routes

        network_interfaces = await network_map_routes.asset_interfaces_context(company_id, asset_id)
    # Script runs belong to the RMM pack; the card disappears with it.
    rmm_context = None
    if (not customer_safe and main_module._feature_pack_available("rmm")
            and (is_super_admin or main_module._membership_menu_can(user, membership, "menu.rmm_scripts"))):
        from app.features.rmm import routes as rmm_routes

        rmm_context = await rmm_routes.asset_rmm_context(
            company_id, asset_id,
            can_run=is_super_admin or main_module._membership_menu_can(
                user, membership, "menu.rmm_scripts", write=True),
        )
    return await main_module._render_template(
        "assets/detail.html", request, user, extra={
            "title": str(record.get("name") or f"Asset {asset_id}"),
            "asset": dict(record), "company": company,
            "references": references, "custom_fields": custom_fields,
            "tickets": [] if customer_safe else await asset_repo.list_tickets_for_asset(asset_id),
            "required_fields": required_fields, "required_missing": required_missing,
            "relationships": relationships,
            "relationship_types": _RELATIONSHIP_TYPES,
            "relationship_assets": [a for a in company_assets if int(a["id"]) != asset_id],
            "relationship_articles": visible_articles,
            "relationship_targets": relationship_targets if can_view_relationships else [],
            "linked_runbooks": linked_runbooks,
            "linked_websites": [] if customer_safe else await websites_repo.list_for_asset(company_id, asset_id),
            "can_edit": not customer_safe and can_write,
            "asset_type": asset_types.get(asset_types.effective(record)),
            "asset_type_label": asset_types.display_label(record),
            "derived_asset_type": asset_types.get(asset_types.derive(
                record.get("type"), form_factor=record.get("form_factor"),
                os_name=record.get("os_name"), machine_type=record.get("machine_type"))),
            **await _asset_type_picker(company_id),
            "synced_asset_type_groups": asset_types.grouped(),
            "reconciliation_candidates": [] if customer_safe else await asset_repo.list_reconciliation_candidates(company_id, asset_id),
            "asset_sources": [] if customer_safe else await asset_repo.list_asset_sources(company_id, asset_id),
            "customer_safe": customer_safe,
            "customer_roles": await role_repo.list_roles() if can_write else [],
            "customer_role_ids": await audience_repo.list_role_ids(company_id, "asset", asset_id) if can_write else [],
            "bcp_context": bcp_context,
            "infrastructure_links": infrastructure_links,
            "network_interfaces": network_interfaces,
            "rmm": rmm_context,
            "can_edit_network_map": can_view_network_map and (is_super_admin or main_module._membership_menu_can(
                user, membership, "menu.network_map", write=True)),
            "asset_photos": (await asset_photo_repo.list_for_asset(
                company_id, asset_id, customer_only=customer_safe
            )) if can_view_photos else [],
            "can_view_asset_photos": can_view_photos,
            "can_view_asset_relationships": can_view_relationships,
            "can_edit_asset_photos": can_edit_photos,
            "can_edit_asset_relationships": can_edit_relationships,
        }
    )


async def _photo_context(request: Request, asset_id: int, *, write: bool = False):
    user, membership, _company, company_id, redirect = await _load_asset_context(request)
    if redirect:
        raise HTTPException(status_code=403, detail="Asset access denied")
    main_module = _main()
    can_view_photos = bool(user.get("is_super_admin")) or main_module._membership_menu_can(
        user, membership, "menu.asset_photos"
    )
    can_write = bool(user.get("is_super_admin")) or main_module._membership_menu_can(
        user, membership, "menu.asset_photos", write=True
    )
    can_manage_asset = bool(user.get("is_super_admin")) or main_module._membership_menu_can(
        user, membership, "menu.assets", write=True
    )
    if not can_view_photos:
        raise HTTPException(status_code=403, detail="Asset photo access denied")
    asset = await asset_repo.get_asset_by_id(asset_id)
    if (not asset or int(asset.get("company_id") or 0) != company_id
            or (not can_manage_asset and (
                not bool(asset.get("customer_visible"))
                or not await _customer_role_can_view_asset(membership, company_id, asset_id)
            ))):
        raise HTTPException(status_code=404, detail="Asset not found")
    if write and not can_write:
        raise HTTPException(status_code=403, detail="Asset photo write access required")
    return user, company_id, can_write


@router.post("/assets/{asset_id}/photos", response_class=JSONResponse)
async def upload_asset_photo(
    request: Request, asset_id: int, photo: UploadFile = File(...),
    caption: str = Form(""), idempotency_key: str = Form(...),
    customer_visible: bool = Form(False),
):
    """Attach a content-validated photo to the authorised canonical asset."""
    user, company_id, _ = await _photo_context(request, asset_id, write=True)
    key = idempotency_key.strip()
    if not key or len(key) > 64 or not all(c.isalnum() or c in "-_" for c in key):
        raise HTTPException(status_code=422, detail="Invalid upload retry key")
    existing = await asset_photo_repo.get_by_key(company_id, asset_id, key)
    if existing:
        await photo.close()
        return JSONResponse({"id": existing["id"], "duplicate": True}, status_code=200)
    clean_caption = caption.strip()[:500] or None
    prepared = await asset_photo_service.prepare(photo, company_id, asset_id)
    try:
        created = await asset_photo_repo.create(
            asset_id=asset_id, company_id=company_id, caption=clean_caption,
            sort_order=0, customer_visible=customer_visible,
            idempotency_key=key, uploaded_by=int(user["id"]), **prepared,
        )
    except Exception:
        asset_photo_service.remove(company_id, asset_id, str(prepared["storage_name"]), str(prepared["thumbnail_name"]))
        # A concurrent retry may have won the unique idempotency-key insert.
        existing = await asset_photo_repo.get_by_key(company_id, asset_id, key)
        if existing:
            return JSONResponse({"id": existing["id"], "duplicate": True}, status_code=200)
        raise
    await audit_service.record(
        action="asset.photo.upload", request=request, user_id=int(user["id"]),
        entity_type="asset", entity_id=asset_id,
        after={"photo_id": created["id"], "size_bytes": created["size_bytes"],
               "customer_visible": bool(created["customer_visible"])},
        metadata={"company_id": company_id},
    )
    return JSONResponse({"id": created["id"], "duplicate": False}, status_code=201)


@router.get("/assets/{asset_id}/photos/{photo_id}/{variant}", response_class=FileResponse)
async def get_asset_photo(request: Request, asset_id: int, photo_id: int, variant: str):
    _user, company_id, can_write = await _photo_context(request, asset_id)
    item = await asset_photo_repo.get(company_id, asset_id, photo_id)
    if not item or (not can_write and not bool(item.get("customer_visible"))):
        raise HTTPException(status_code=404, detail="Photo not found")
    if variant not in {"original", "thumbnail"}:
        raise HTTPException(status_code=404, detail="Photo not found")
    file_path = asset_photo_service.path(
        company_id, asset_id, item["storage_name" if variant == "original" else "thumbnail_name"]
    )
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(file_path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/assets/{asset_id}/photos/{photo_id}")
async def update_asset_photo(request: Request, asset_id: int, photo_id: int):
    user, company_id, _ = await _photo_context(request, asset_id, write=True)
    item = await asset_photo_repo.get(company_id, asset_id, photo_id)
    if not item:
        raise HTTPException(status_code=404, detail="Photo not found")
    form = await request.form()
    try:
        order = max(0, min(10000, int(form.get("sort_order") or 0)))
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid photo order")
    caption = str(form.get("caption") or "").strip()[:500] or None
    visible = str(form.get("customer_visible") or "").lower() in {"1", "true", "on"}
    await asset_photo_repo.update(company_id, asset_id, photo_id, caption=caption, sort_order=order, customer_visible=visible)
    await audit_service.record(action="asset.photo.update", request=request, user_id=int(user["id"]), entity_type="asset", entity_id=asset_id, after={"photo_id": photo_id, "sort_order": order, "customer_visible": visible}, metadata={"company_id": company_id})
    return _main().flash_redirect(f"/assets/{asset_id}#asset-photos", "Photo updated.", "success")


@router.post("/assets/{asset_id}/photos/{photo_id}/delete")
async def delete_asset_photo(request: Request, asset_id: int, photo_id: int):
    user, company_id, _ = await _photo_context(request, asset_id, write=True)
    item = await asset_photo_repo.get(company_id, asset_id, photo_id)
    if not item:
        raise HTTPException(status_code=404, detail="Photo not found")
    await asset_photo_repo.delete(company_id, asset_id, photo_id)
    asset_photo_service.remove(company_id, asset_id, item["storage_name"], item["thumbnail_name"])
    await audit_service.record(action="asset.photo.delete", request=request, user_id=int(user["id"]), entity_type="asset", entity_id=asset_id, before={"photo_id": photo_id, "size_bytes": item["size_bytes"]}, metadata={"company_id": company_id})
    return _main().flash_redirect(f"/assets/{asset_id}#asset-photos", "Photo deleted.", "success")


async def _rack_image_context(request: Request, *, write: bool = False):
    main_module = _main()
    user, membership, _company, company_id, redirect = await _load_asset_context(request, "menu.racks")
    if redirect:
        raise HTTPException(status_code=403, detail="Rack access denied")
    if write and not (user.get("is_super_admin") or main_module._membership_menu_can(
            user, membership, "menu.racks", write=True)):
        raise HTTPException(status_code=403, detail="Rack image write access required")
    return user, company_id


def _rack_image_item_type(value: str) -> str:
    try:
        return rack_item_types.normalise(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Unknown item type") from exc


def _rack_image_kind(value: str) -> str:
    kind = str(value or "").strip().lower()
    if kind not in {"device", "product"}:
        raise HTTPException(status_code=422, detail="Image kind must be device or product")
    return kind


@router.get("/api/infrastructure/rack-item-images/library", response_class=JSONResponse,
            summary="List rack item library images for a type")
async def list_rack_item_library(request: Request, item_type: str = Query(...),
                                 kind: str | None = Query(None)):
    _user, company_id = await _rack_image_context(request)
    clean_type = _rack_image_item_type(item_type)
    kind_value = str(kind or "").strip().lower() if kind else ""
    clean_kind = kind_value if kind_value in {"device", "product"} else None
    return JSONResponse(await rack_image_repo.list_library(company_id, clean_type, clean_kind))


@router.get("/api/infrastructure/rack-item-images/products/search", response_class=JSONResponse,
            summary="Search shop products for the rack image import picker")
async def search_rack_image_products(request: Request, q: str = Query(..., min_length=1),
                                     limit: int = Query(8, ge=1, le=25)):
    _user, company_id = await _rack_image_context(request)
    results = await shop_repo.search_products_for_company_lookup(q, company_id=company_id, limit=limit)
    return JSONResponse(results)


async def _serve_rack_image(request: Request, image_id: int, variant: str) -> FileResponse:
    _user, company_id = await _rack_image_context(request)
    row = await rack_image_repo.get_image(company_id, image_id)
    if not row:
        raise HTTPException(status_code=404, detail="Image not found")
    serve = "full" if (variant == "thumb" and not row.get("thumbnail_name")) else variant
    path = rack_image_service.resolve_rack_image(str(row["storage_name"]), serve)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type=str(row.get("content_type") or "image/jpeg"),
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/api/infrastructure/rack-item-images/{image_id}", response_class=FileResponse,
            summary="Serve a rack item image")
async def get_rack_item_image(request: Request, image_id: int):
    return await _serve_rack_image(request, image_id, "full")


@router.get("/api/infrastructure/rack-item-images/{image_id}/thumb", response_class=FileResponse,
            summary="Serve a rack item image thumbnail")
async def get_rack_item_image_thumb(request: Request, image_id: int):
    return await _serve_rack_image(request, image_id, "thumb")


@router.post("/api/infrastructure/rack-item-images", response_class=JSONResponse, status_code=201,
             summary="Upload a rack item image to the library")
async def upload_rack_item_image(request: Request, image: UploadFile = File(...),
                                 item_type: str = Form(...), kind: str = Form("device"),
                                 caption: str = Form("")):
    user, company_id = await _rack_image_context(request, write=True)
    clean_type, clean_kind = _rack_image_item_type(item_type), _rack_image_kind(kind)
    prepared = await rack_image_service.prepare_rack_image(image, company_id, clean_type, clean_kind)
    try:
        image_id, created = await rack_image_repo.create_image(
            company_id, clean_type, clean_kind,
            caption=str(caption or "").strip()[:191] or None, uploaded_by=int(user["id"]), **prepared)
    except Exception:
        rack_image_service.remove_rack_image(str(prepared["storage_name"]), prepared["thumbnail_name"])
        raise
    if not created:
        # Identical bytes already exist in the library; drop the files just written.
        rack_image_service.remove_rack_image(str(prepared["storage_name"]), prepared["thumbnail_name"])
    await audit_service.record(action="infrastructure.rack_item_image.upload", request=request,
                               user_id=int(user["id"]), entity_type="rack_item_image", entity_id=image_id,
                               after={"company_id": company_id, "item_type": clean_type, "kind": clean_kind,
                                      "duplicate": not created}, metadata={"company_id": company_id})
    return JSONResponse({"id": image_id, "duplicate": not created}, status_code=201 if created else 200)


@router.delete("/api/infrastructure/rack-item-images/{image_id}", response_class=JSONResponse,
               summary="Delete a rack item image from the library")
async def delete_rack_item_image(request: Request, image_id: int):
    user, company_id = await _rack_image_context(request, write=True)
    row = await rack_image_repo.delete_image(company_id, image_id)
    if not row:
        raise HTTPException(status_code=404, detail="Image not found")
    if not await rack_image_repo.storage_is_referenced(str(row["storage_name"])):
        rack_image_service.remove_rack_image(str(row["storage_name"]), row.get("thumbnail_name"))
    await audit_service.record(action="infrastructure.rack_item_image.delete", request=request,
                               user_id=int(user["id"]), entity_type="rack_item_image", entity_id=image_id,
                               before={"company_id": company_id, "item_type": row.get("item_type"),
                                       "kind": row.get("kind")}, metadata={"company_id": company_id})
    return JSONResponse({"id": image_id, "deleted": True})


@router.post("/api/infrastructure/rack-equipment/{equipment_id}/images/{image_id}/attach",
             response_class=JSONResponse, summary="Attach a library image to a rack item")
async def attach_rack_item_image(request: Request, equipment_id: int, image_id: int):
    user, company_id = await _rack_image_context(request, write=True)
    try:
        await rack_image_repo.attach(company_id, equipment_id, image_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit_service.record(action="infrastructure.rack_item_image.attach", request=request,
                               user_id=int(user["id"]), entity_type="rack_equipment", entity_id=equipment_id,
                               after={"company_id": company_id, "image_id": image_id},
                               metadata={"company_id": company_id})
    return JSONResponse({"equipment_id": equipment_id, "image_id": image_id, "attached": True})


@router.post("/api/infrastructure/rack-equipment/{equipment_id}/images/{image_id}/detach",
             response_class=JSONResponse, summary="Detach a library image from a rack item")
async def detach_rack_item_image(request: Request, equipment_id: int, image_id: int):
    user, company_id = await _rack_image_context(request, write=True)
    await rack_image_repo.detach(company_id, equipment_id, image_id)
    await audit_service.record(action="infrastructure.rack_item_image.detach", request=request,
                               user_id=int(user["id"]), entity_type="rack_equipment", entity_id=equipment_id,
                               before={"company_id": company_id, "image_id": image_id},
                               metadata={"company_id": company_id})
    return JSONResponse({"equipment_id": equipment_id, "image_id": image_id, "detached": True})


@router.post("/api/infrastructure/rack-item-images/import-shop", response_class=JSONResponse,
             status_code=201, summary="Import a shop product image into the library",
             description="Imports a local shop image once and reuses its stored files on subsequent imports, "
                         "including across companies. Library entries and attachments remain company-scoped. "
                         "Returns 200 with duplicate=true when reusing an image, or 201 for a new import.")
async def import_shop_rack_item_image(request: Request):
    user, company_id = await _rack_image_context(request, write=True)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid JSON body") from None
    payload = payload if isinstance(payload, dict) else {}
    try:
        product_id = int(payload.get("product_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="product_id is required") from None
    clean_type = _rack_image_item_type(str(payload.get("item_type") or ""))
    clean_kind = _rack_image_kind(str(payload.get("kind") or "product"))
    product = await shop_repo.get_product_by_id(product_id, include_archived=True, company_id=company_id)
    image_url = (product or {}).get("image_url")
    if not image_url:
        raise HTTPException(status_code=422, detail="That product has no image to import")
    existing = await rack_image_repo.find_shop_image(company_id, clean_type, clean_kind, product_id)
    if existing and not rack_image_service.resolve_rack_image(str(existing["storage_name"])).is_file():
        existing = None
    if existing:
        if int(existing["company_id"]) == company_id and existing["item_type"] == clean_type:
            image_id = int(existing["id"])
        else:
            # Give this company its own library entry pointing at the same files.
            image_id, _ = await rack_image_repo.create_image(
                company_id, clean_type, clean_kind, source_product_id=product_id,
                uploaded_by=int(user["id"]),
                **{key: existing[key] for key in (
                    "storage_name", "thumbnail_name", "content_type", "size_bytes", "content_hash")})
        created = False
    else:
        bundle = rack_image_service.read_shop_product_image(str(image_url))
        if not bundle:
            raise HTTPException(status_code=422, detail="That product image cannot be imported")
        image_bytes, filename, _content_type = bundle
        prepared = await rack_image_service.prepare_bytes_rack_image(
            image_bytes, filename, company_id, clean_type, clean_kind)
        try:
            image_id, created = await rack_image_repo.create_image(
                company_id, clean_type, clean_kind, source_product_id=product_id,
                uploaded_by=int(user["id"]), **prepared)
        except Exception:
            rack_image_service.remove_rack_image(str(prepared["storage_name"]), prepared["thumbnail_name"])
            raise
        if not created:
            rack_image_service.remove_rack_image(str(prepared["storage_name"]), prepared["thumbnail_name"])
    await audit_service.record(action="infrastructure.rack_item_image.import_shop", request=request,
                               user_id=int(user["id"]), entity_type="rack_item_image", entity_id=image_id,
                               after={"company_id": company_id, "item_type": clean_type, "kind": clean_kind,
                                      "product_id": product_id, "duplicate": not created},
                               metadata={"company_id": company_id})
    return JSONResponse({"id": image_id, "duplicate": not created}, status_code=201 if created else 200)


@router.get("/api/infrastructure/rack-equipment/{equipment_id}/images", response_class=JSONResponse,
            summary="List images attached to a rack item")
async def list_rack_equipment_images(request: Request, equipment_id: int):
    _user, company_id = await _rack_image_context(request)
    return JSONResponse(await rack_image_repo.list_equipment_images(company_id, equipment_id))


@router.get("/asset-exports/csv")
async def export_assets(request: Request) -> Response:
    """Export only customer-safe fields for records the actor may access."""
    user, membership, _company, company_id, redirect = await _load_asset_context(request)
    if redirect:
        raise HTTPException(status_code=403, detail="Asset access denied")
    main_module = _main()
    if not (user.get("is_super_admin") or main_module._membership_menu_can(user, membership, "menu.assets")):
        raise HTTPException(status_code=403, detail="Asset export access denied")
    records = await asset_repo.list_company_assets(company_id)
    if not user.get("is_super_admin"):
        records = [row for row in records if bool(row.get("customer_visible")) and await _customer_role_can_view_asset(membership, company_id, int(row["id"]))]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(_CUSTOMER_ASSET_FIELDS), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(_customer_asset(dict(row)) for row in records)
    await audit_service.record(
        action="asset.export", request=request, user_id=int(user["id"]),
        entity_type="company", entity_id=company_id,
        after={"company_id": company_id, "record_count": len(records), "format": "csv"},
        metadata={"company_id": company_id},
    )
    return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="assets-company-{company_id}.csv"',
        "Cache-Control": "private, no-store",
    })


@router.post("/assets/{asset_id}/publish")
async def publish_asset(request: Request, asset_id: int):
    user, _membership, _, company_id, redirect = await _load_asset_context(request)
    if redirect or not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin privileges required")
    record = await asset_repo.get_asset_by_id(asset_id)
    if not record or int(record.get("company_id") or 0) != company_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    form = await request.form()
    visible = str(form.get("action") or "publish") == "publish"
    await asset_repo.set_customer_visible(asset_id, visible)
    role_ids = form.getlist("allowed_role_ids") if visible else []
    await audience_repo.replace_roles(company_id, "asset", asset_id, [int(value) for value in role_ids])
    await audit_service.record(
        action="asset.publish" if visible else "asset.unpublish", request=request,
        user_id=int(user["id"]), entity_type="asset", entity_id=asset_id,
        before={"customer_visible": bool(record.get("customer_visible"))},
        after={"customer_visible": visible, "allowed_role_ids": [int(value) for value in role_ids]}, metadata={"company_id": company_id},
    )
    return _main().flash_redirect(f"/assets/{asset_id}",
                                  "Customer visibility updated.", "success")


@router.post("/assets/{asset_id}/relationships")
async def create_asset_relationship(request: Request, asset_id: int):
    main_module = _main()
    user, membership, _, company_id, redirect = await _load_asset_context(request)
    if redirect:
        return redirect
    if not (user.get("is_super_admin") or main_module._membership_menu_can(
        user, membership, "menu.asset_relationships", write=True
    )):
        raise HTTPException(status_code=403, detail="Asset relationship write access required")
    source = await asset_repo.get_asset_by_id(asset_id)
    if not source or int(source.get("company_id") or 0) != company_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    form = await request.form()
    target_key = str(form.get("target_key") or "")
    target_type = str(form.get("target_type") or "")
    relationship_type = str(form.get("relationship_type") or "")
    try:
        if target_key:
            target_type, raw_target_id = target_key.split(":", 1)
        else:
            raw_target_id = form.get("target_id") or 0
        target_id = int(raw_target_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid relationship target")
    if relationship_type not in _RELATIONSHIP_TYPES:
        raise HTTPException(status_code=422, detail="Invalid relationship type")
    if target_type not in _RELATIONSHIP_TARGET_TYPES or target_id < 1:
        raise HTTPException(status_code=422, detail="Unsupported relationship target")
    if target_type == "asset":
        target = await asset_repo.get_asset_by_id(target_id)
        if (not target or int(target.get("company_id") or 0) != company_id
                or target_id == asset_id or target.get("archived_at")):
            raise HTTPException(status_code=404, detail="Relationship target not found")
    elif target_type == "knowledge_base_article":
        context = await knowledge_base_service.build_access_context(user)
        visible = await knowledge_base_service.list_articles_for_context(
            context, include_unpublished=bool(user.get("is_super_admin"))
        )
        if target_id not in {int(article["id"]) for article in visible}:
            raise HTTPException(status_code=404, detail="Relationship target not found")
    elif target_type == "ticket":
        if not (user.get("is_super_admin") or main_module._membership_menu_can(user, membership, "menu.tickets", write=True)):
            raise HTTPException(status_code=404, detail="Relationship target not found")
        target = await tickets_repo.get_ticket(target_id)
        if not target or int(target.get("company_id") or 0) != company_id or target.get("merged_into_ticket_id"):
            raise HTTPException(status_code=404, detail="Relationship target not found")
    elif target_type == "process_run":
        if not (user.get("is_super_admin") or main_module._membership_menu_can(user, membership, "menu.processes")):
            raise HTTPException(status_code=404, detail="Relationship target not found")
        target = await processes_repo.get_run(company_id, target_id)
        if not target or target.get("status") == "cancelled":
            raise HTTPException(status_code=404, detail="Relationship target not found")
    elif target_type == "website":
        if not (user.get("is_super_admin") or main_module._membership_menu_can(user, membership, "menu.websites")):
            raise HTTPException(status_code=404, detail="Relationship target not found")
        if not await websites_repo.get_website(company_id, target_id):
            raise HTTPException(status_code=404, detail="Relationship target not found")
    elif target_type in {"ip_network", "rack"}:
        target_permission = "menu.ipam" if target_type == "ip_network" else "menu.racks"
        if not (user.get("is_super_admin") or main_module._membership_menu_can(user, membership, target_permission)):
            raise HTTPException(status_code=404, detail="Relationship target not found")
        table = "ip_networks" if target_type == "ip_network" else "racks"
        if not await infrastructure_repo.get_record(table, company_id, target_id):
            raise HTTPException(status_code=404, detail="Relationship target not found")
    created = await asset_repo.create_relationship(
        company_id=company_id, source_asset_id=asset_id, target_type=target_type,
        target_id=target_id, relationship_type=relationship_type,
        created_by=int(user["id"]),
    )
    if not created:
        raise HTTPException(status_code=409, detail="Relationship already exists")
    await audit_service.record(
        action="asset.relationship.create", request=request, entity_type="asset",
        entity_id=asset_id, after={"target_type": target_type,
                                   "target_id": target_id,
                                   "relationship_type": relationship_type},
    )
    return main_module.flash_redirect(f"/assets/{asset_id}", "Relationship added.", "success")


@router.post("/assets/{asset_id}/relationships/{relationship_id}/delete")
async def delete_asset_relationship(request: Request, asset_id: int, relationship_id: int):
    main_module = _main()
    user, membership, _, company_id, redirect = await _load_asset_context(request)
    if redirect:
        return redirect
    if not (user.get("is_super_admin") or main_module._membership_menu_can(
        user, membership, "menu.asset_relationships", write=True
    )):
        raise HTTPException(status_code=403, detail="Asset relationship write access required")
    asset = await asset_repo.get_asset_by_id(asset_id)
    if not asset or int(asset.get("company_id") or 0) != company_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    relationships = await asset_repo.list_relationships_for_asset(company_id, asset_id)
    if relationship_id not in {int(row["id"]) for row in relationships}:
        raise HTTPException(status_code=404, detail="Relationship not found")
    await asset_repo.delete_relationship(company_id, relationship_id)
    await audit_service.record(action="asset.relationship.delete", request=request,
                               entity_type="asset", entity_id=asset_id,
                               before={"relationship_id": relationship_id})
    return main_module.flash_redirect(f"/assets/{asset_id}", "Relationship removed.", "success")


@router.post("/assets/{asset_id}")
async def update_asset_documentation(request: Request, asset_id: int):
    main_module = _main()
    user, membership, _, company_id, redirect = await _load_asset_context(request)
    if redirect:
        return redirect
    if not (user.get("is_super_admin") or main_module._membership_menu_can(
        user, membership, "menu.assets", write=True
    )):
        raise HTTPException(status_code=403, detail="Asset write access required")
    record = await asset_repo.get_asset_by_id(asset_id)
    if not record or int(record.get("company_id") or 0) != company_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    form = await request.form()
    if record.get("provenance") == "manual":
        values = await _manual_asset_values(form, company_id, record)
        await asset_repo.update_manual_inventory(asset_id, **values)
        await _save_submitted_custom_fields(asset_id, form)
    criticality = _clean_optional(form, "criticality")
    review_status = str(form.get("review_status") or "not_reviewed")
    if criticality not in {None, "low", "medium", "high", "critical"}:
        raise HTTPException(status_code=422, detail="Invalid criticality")
    if review_status not in {"not_reviewed", "needs_review", "reviewed"}:
        raise HTTPException(status_code=422, detail="Invalid review status")
    references = _parse_external_references(str(form.get("external_references") or ""))
    await asset_repo.update_operational_documentation(
        asset_id, owner=_clean_optional(form, "owner"),
        support_contact=_clean_optional(form, "support_contact"),
        criticality=criticality, location=_clean_optional(form, "location"),
        operational_notes=_clean_optional(form, "operational_notes"),
        review_status=review_status,
        external_references_json=json.dumps(references) if references else None,
    )
    await audit_service.record(
        action="asset.documentation.update", request=request,
        entity_type="asset", entity_id=asset_id,
        after={"criticality": criticality, "review_status": review_status,
               "reference_count": len(references), "has_notes": bool(_clean_optional(form, "operational_notes"))},
    )
    return main_module.flash_redirect(f"/assets/{asset_id}", "Asset documentation saved.", "success")


@router.post("/assets/{asset_id}/asset-type", summary="Override or reset a synced asset's type")
async def update_asset_type(request: Request, asset_id: int):
    user, _company, company_id, redirect = await _asset_write_context(request)
    if redirect:
        return redirect
    record = await asset_repo.get_asset_by_id(asset_id)
    if not record or int(record.get("company_id") or 0) != company_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    form = await request.form()
    submitted = str(form.get("asset_type") or "").strip() or None
    try:
        await asset_repo.set_asset_type(company_id, asset_id, submitted)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Choose an asset type from the list") from exc
    await audit_service.record(
        action="asset.type.update", request=request, user_id=int(user["id"]),
        entity_type="asset", entity_id=asset_id,
        before={"asset_type": record.get("asset_type"), "source": record.get("asset_type_source")},
        after={"asset_type": submitted, "source": "manual" if submitted else "auto"},
    )
    return _main().flash_redirect(f"/assets/{asset_id}", "Asset type saved.", "success")


@router.post("/assets/{asset_id}/reconciliation/{source_record_id}/approve")
async def approve_asset_reconciliation(request: Request, asset_id: int, source_record_id: int):
    user, _company, company_id, redirect = await _asset_write_context(request)
    if redirect:
        return redirect
    record = await asset_repo.get_asset_by_id(asset_id)
    if not record or int(record.get("company_id") or 0) != company_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not await asset_repo.approve_reconciliation(company_id, asset_id, source_record_id):
        raise HTTPException(status_code=404, detail="Possible match not found")
    await audit_service.record(
        action="asset.reconciliation.approve", request=request, user_id=int(user["id"]),
        entity_type="asset", entity_id=asset_id,
        after={"source_record_id": source_record_id, "canonical_asset_id": asset_id},
    )
    return _main().flash_redirect(
        f"/assets/{asset_id}", "Integration match approved. Manual documentation was preserved.", "success"
    )


@router.post("/assets/{asset_id}/archive")
async def archive_asset(request: Request, asset_id: int):
    main_module = _main()
    user, membership, _, company_id, redirect = await _load_asset_context(request)
    if redirect:
        return redirect
    if not (user.get("is_super_admin") or main_module._membership_menu_can(
        user, membership, "menu.assets", write=True
    )):
        raise HTTPException(status_code=403, detail="Asset write access required")
    record = await asset_repo.get_asset_by_id(asset_id)
    if not record or int(record.get("company_id") or 0) != company_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    form = await request.form()
    archived = str(form.get("action") or "archive") != "restore"
    await asset_repo.set_asset_archived(asset_id, archived)
    await audit_service.record(
        action="asset.archive" if archived else "asset.restore", request=request,
        entity_type="asset", entity_id=asset_id, after={"archived": archived},
    )
    message = "Asset archived." if archived else "Asset restored."
    return main_module.flash_redirect(f"/assets/{asset_id}", message, "success")


@router.delete("/assets/{asset_id}", response_class=JSONResponse)
async def delete_asset(request: Request, asset_id: int):
    user, _membership, _, company_id, redirect = await _load_asset_context(request)
    if redirect:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Asset management access denied",
        )
    if not user.get("is_super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin privileges required",
        )

    record = await asset_repo.get_asset_by_id(asset_id)
    if not record or int(record.get("company_id", 0) or 0) != company_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found"
        )

    await asset_repo.delete_asset(asset_id)
    log_info(
        "Asset deleted",
        asset_id=asset_id,
        company_id=company_id,
        user_id=user.get("id"),
    )
    return JSONResponse({"success": True})


__all__ = ["router"]
