"""Assets routes for the ``assets`` feature pack."""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.core.logging import log_info
from app.repositories import asset_custom_fields as asset_custom_fields_repo
from app.repositories import assets as asset_repo
from app.repositories import companies as company_repo
from app.repositories import network_devices as network_devices_repo
from app.repositories import tray as tray_repo
from app.services import tray as tray_service
from app.services import hudu as hudu_service
from app.services import audit as audit_service

router = APIRouter(tags=["Assets"])


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

    rows = await asset_repo.list_company_assets(company_id)
    field_definitions = await asset_custom_fields_repo.list_field_definitions()

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
            "type": _clean_text(row.get("type")),
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
        "is_super_admin": bool(user.get("is_super_admin")),
        "has_asset_actions": has_asset_actions,
        "matrix_enabled": main_module.settings.matrix_enabled,
    }
    return await main_module._render_template(
        "assets/index.html", request, user, extra=extra
    )


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
            "can_sync_hudu": bool(company.get("hudu_id")),
        },
    )


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
async def asset_detail_page(request: Request, asset_id: int):
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_asset_context(request)
    if redirect:
        return redirect

    record = await asset_repo.get_asset_by_id(asset_id)
    record_company_id = record.get("company_id") if record else None
    if record_company_id is None or int(record_company_id) != company_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found"
        )

    try:
        references = json.loads(record.get("external_references_json") or "[]")
    except (TypeError, ValueError):
        references = []
    if not isinstance(references, list):
        references = []
    definitions = await asset_custom_fields_repo.list_field_definitions()
    values = await asset_custom_fields_repo.get_all_asset_field_values([asset_id])
    custom_fields = [
        {**definition, "value": values.get(asset_id, {}).get(definition["id"])}
        for definition in definitions
    ]
    required_fields = await asset_repo.list_required_fields(record.get("type"))
    required_missing = [
        key for key in required_fields
        if not record.get(key) and not any(
            str(field.get("id")) == key.removeprefix("custom:") and field.get("value")
            for field in custom_fields
        )
    ]
    return await main_module._render_template(
        "assets/detail.html", request, user, extra={
            "title": str(record.get("name") or f"Asset {asset_id}"),
            "asset": dict(record), "company": company,
            "references": references, "custom_fields": custom_fields,
            "tickets": await asset_repo.list_tickets_for_asset(asset_id),
            "required_fields": required_fields, "required_missing": required_missing,
            "can_edit": bool(user.get("is_super_admin")) or main_module._membership_menu_can(
                user, membership, "menu.assets", write=True
            ),
        }
    )


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
