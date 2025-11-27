from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.logging import log_error, log_info
from app.repositories import assets as assets_repo
from app.repositories import companies as company_repo
from app.services import syncro, tacticalrmm


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


async def import_tactical_assets_for_company(
    company_id: int,
    *,
    tactical_client_id: str | None = None,
) -> int:
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise ValueError(f"Company {company_id} not found")
    client_identifier = tactical_client_id or company.get("tacticalrmm_client_id")
    if not client_identifier:
        raise tacticalrmm.TacticalRMMConfigurationError("Company is missing a Tactical RMM mapping")

    client_id = str(client_identifier).strip()
    log_info(
        "Starting Tactical RMM asset import",
        company_id=company_id,
        tactical_client_id=client_id,
    )
    agents = await tacticalrmm.fetch_agents(client_id)
    processed = 0
    seen: set[tuple[str | None, str | None, str]] = set()

    for agent in agents:
        if not isinstance(agent, Mapping):
            continue
        details = tacticalrmm.extract_agent_details(agent)
        name = _clean_string(details.get("name")) or "Asset"
        serial = _clean_string(details.get("serial_number"))
        tactical_id = _clean_string(details.get("tactical_asset_id"))
        dedupe_key = (tactical_id, serial, name.lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        await assets_repo.upsert_asset(
            company_id=company_id,
            name=name,
            type=_clean_string(details.get("type")),
            serial_number=serial,
            status=_clean_string(details.get("status")),
            os_name=_clean_string(details.get("os_name")),
            cpu_name=_clean_string(details.get("cpu_name")),
            ram_gb=details.get("ram_gb"),
            hdd_size=_clean_string(details.get("hdd_size")),
            last_sync=details.get("last_sync"),
            motherboard_manufacturer=_clean_string(details.get("motherboard_manufacturer")),
            form_factor=_clean_string(details.get("form_factor")),
            last_user=_clean_string(details.get("last_user")),
            approx_age=details.get("approx_age"),
            performance_score=details.get("performance_score"),
            warranty_status=_clean_string(details.get("warranty_status")),
            warranty_end_date=details.get("warranty_end_date"),
            tactical_asset_id=tactical_id,
            match_name=True,
        )
        processed += 1

    log_info(
        "Completed Tactical RMM asset import",
        company_id=company_id,
        tactical_client_id=client_id,
        processed=processed,
        total=len(agents),
    )
    return processed


async def import_all_tactical_assets() -> dict[str, Any]:
    companies = await company_repo.list_companies()
    summary: dict[str, Any] = {
        "processed": 0,
        "companies": {},
        "skipped": [],
    }

    for company in companies:
        raw_company_id = company.get("id")
        try:
            company_id = int(raw_company_id)
        except (TypeError, ValueError):
            continue
        client_identifier = _clean_string(company.get("tacticalrmm_client_id"))
        if not client_identifier:
            summary["skipped"].append(
                {"company_id": company_id, "reason": "missing_mapping"}
            )
            continue
        try:
            processed = await import_tactical_assets_for_company(
                company_id,
                tactical_client_id=client_identifier,
            )
        except tacticalrmm.TacticalRMMConfigurationError as exc:
            log_error(
                "Skipping Tactical RMM asset import",
                company_id=company_id,
                error=str(exc),
            )
            summary["skipped"].append(
                {"company_id": company_id, "reason": str(exc)}
            )
            continue
        except tacticalrmm.TacticalRMMAPIError as exc:
            log_error(
                "Tactical RMM asset import failed",
                company_id=company_id,
                error=str(exc),
            )
            summary["skipped"].append(
                {"company_id": company_id, "reason": str(exc)}
            )
            continue

        summary["companies"][company_id] = {"processed": processed}
        summary["processed"] += processed

    return summary
