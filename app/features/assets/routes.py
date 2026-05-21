"""Assets routes for the ``assets`` feature pack."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.logging import log_info
from app.repositories import asset_custom_fields as asset_custom_fields_repo
from app.repositories import assets as asset_repo


router = APIRouter(tags=["Assets"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/assets", response_class=HTMLResponse)
async def assets_page(request: Request):
    main_module = _main()
    user, _membership, company, company_id, redirect = await main_module._load_asset_context(request)
    if redirect:
        return redirect

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
            "serial_number": _clean_text(row.get("serial_number")),
            "status": _clean_text(row.get("status")),
            "os_name": _clean_text(row.get("os_name")),
            "cpu_name": _clean_text(row.get("cpu_name")),
            "hdd_size": _clean_text(row.get("hdd_size")),
            "motherboard_manufacturer": _clean_text(row.get("motherboard_manufacturer")),
            "form_factor": _clean_text(row.get("form_factor")),
            "last_user": _clean_text(row.get("last_user")),
            "warranty_status": _clean_text(row.get("warranty_status")),
            "syncro_asset_id": _clean_text(row.get("syncro_asset_id")),
            "tactical_asset_id": _clean_text(row.get("tactical_asset_id")),
        }

        ram_display, ram_sort = _format_number(row.get("ram_gb"))
        approx_display, approx_sort = _format_number(row.get("approx_age"))
        performance_display, performance_sort = _format_number(row.get("performance_score"))
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
    cf_values_by_asset = await asset_custom_fields_repo.get_all_asset_field_values(asset_ids)

    for record in prepared:
        asset_id = record.get("id")
        asset_cf = cf_values_by_asset.get(asset_id, {})
        for field_def in field_definitions:
            key = f"cf_{field_def['id']}"
            record[key] = asset_cf.get(field_def["id"])

    custom_columns = [
        {
            "key": f"cf_{field_def['id']}",
            "label": field_def["display_name"] or field_def["name"],
            "sort": "date" if field_def["field_type"] == "date" else (
                "number" if field_def["field_type"] == "checkbox" else "string"
            ),
            "field_type": field_def["field_type"],
        }
        for field_def in field_definitions
    ]
    all_columns = list(main_module._ASSET_TABLE_COLUMNS) + custom_columns

    stats = {
        "total": len(prepared),
        "recent_sync": recent_sync,
        "expired_warranty": expired_warranty,
        "active_warranty": active_warranty,
    }

    extra = {
        "title": "Assets",
        "assets": prepared,
        "columns": all_columns,
        "company": company,
        "stats": stats,
        "has_assets": bool(prepared),
        "is_super_admin": bool(user.get("is_super_admin")),
    }
    return await main_module._render_template("assets/index.html", request, user, extra=extra)


@router.get("/assets/settings", response_class=HTMLResponse)
async def assets_settings_page(request: Request):
    main_module = _main()
    user, _membership, _, _, redirect = await main_module._load_asset_context(request)
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
    }
    return await main_module._render_template("assets/settings.html", request, user, extra=extra)


@router.delete("/assets/{asset_id}", response_class=JSONResponse)
async def delete_asset(request: Request, asset_id: int):
    main_module = _main()
    user, _membership, _, company_id, redirect = await main_module._load_asset_context(request)
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    await asset_repo.delete_asset(asset_id)
    log_info(
        "Asset deleted",
        asset_id=asset_id,
        company_id=company_id,
        user_id=user.get("id"),
    )
    return JSONResponse({"success": True})


__all__ = ["router"]
