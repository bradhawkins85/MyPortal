from __future__ import annotations

from typing import Any

from app.core.logging import log_info
from app.repositories import assets as assets_repo
from app.repositories import companies as company_repo
from app.services import syncro


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def import_assets_for_company(
    company_id: int,
    *,
    syncro_company_id: str | None = None,
) -> int:
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise ValueError(f"Company {company_id} not found")
    syncro_id = syncro_company_id or company.get("syncro_company_id")
    if not syncro_id:
        raise syncro.SyncroConfigurationError("Company is missing a Syncro mapping")

    log_info("Starting Syncro asset import", company_id=company_id, syncro_id=syncro_id)
    assets = await syncro.get_assets(syncro_id)
    processed = 0

    for asset in assets:
        if not isinstance(asset, dict):
            continue
        details = syncro.extract_asset_details(asset)
        name = _clean_string(details.get("name") or asset.get("name")) or "Asset"
        type_value = _clean_string(details.get("type"))
        serial = _clean_string(details.get("serial_number"))
        status = _clean_string(details.get("status"))
        os_name = _clean_string(details.get("os_name"))
        cpu_name = _clean_string(details.get("cpu_name"))
        hdd_size = _clean_string(details.get("hdd_size"))
        last_user = _clean_string(details.get("last_user"))
        motherboard = _clean_string(details.get("motherboard_manufacturer"))
        form_factor = _clean_string(details.get("form_factor"))
        warranty_status = _clean_string(details.get("warranty_status"))
        warranty_end = details.get("warranty_end_date")
        last_sync = details.get("last_sync")
        ram_value = details.get("ram_gb")
        approx_age = details.get("cpu_age")
        performance_score = details.get("performance_score")

        syncro_asset_id = asset.get("id") or details.get("id")
        syncro_asset_id = str(syncro_asset_id) if syncro_asset_id is not None else None

        await assets_repo.upsert_asset(
            company_id=company_id,
            name=name,
            type=type_value,
            serial_number=serial,
            status=status,
            os_name=os_name,
            cpu_name=cpu_name,
            ram_gb=ram_value,
            hdd_size=hdd_size,
            last_sync=last_sync,
            motherboard_manufacturer=motherboard,
            form_factor=form_factor,
            last_user=last_user,
            approx_age=approx_age,
            performance_score=performance_score,
            warranty_status=warranty_status,
            warranty_end_date=warranty_end,
            syncro_asset_id=syncro_asset_id,
        )
        processed += 1

    log_info(
        "Completed Syncro asset import",
        company_id=company_id,
        syncro_id=syncro_id,
        processed=processed,
        total=len(assets),
    )
    return processed
