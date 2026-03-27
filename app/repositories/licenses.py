from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from app.core.database import db


_MICROSOFT_LICENSE_FRIENDLY_NAMES: dict[str, str] = {
    "O365_BUSINESS_ESSENTIALS": "Office 365 Business Essentials",
    "O365_BUSINESS_PREMIUM": "Office 365 Business Premium",
    "DESKLESSPACK": "Office 365 (Plan K1)",
    "DESKLESSWOFFPACK": "Office 365 (Plan K2)",
    "LITEPACK": "Office 365 (Plan P1)",
    "EXCHANGESTANDARD": "Office 365 Exchange Online Only",
    "STANDARDPACK": "Enterprise Plan E1",
    "STANDARDWOFFPACK": "Office 365 (Plan E2)",
    "ENTERPRISEPACK": "Enterprise Plan E3",
    "ENTERPRISEPACKLRG": "Enterprise Plan E3",
    "ENTERPRISEWITHSCAL": "Enterprise Plan E4",
    "STANDARDPACK_STUDENT": "Office 365 (Plan A1) for Students",
    "STANDARDWOFFPACKPACK_STUDENT": "Office 365 (Plan A2) for Students",
    "ENTERPRISEPACK_STUDENT": "Office 365 (Plan A3) for Students",
    "ENTERPRISEWITHSCAL_STUDENT": "Office 365 (Plan A4) for Students",
    "STANDARDPACK_FACULTY": "Office 365 (Plan A1) for Faculty",
    "STANDARDWOFFPACKPACK_FACULTY": "Office 365 (Plan A2) for Faculty",
    "ENTERPRISEPACK_FACULTY": "Office 365 (Plan A3) for Faculty",
    "ENTERPRISEWITHSCAL_FACULTY": "Office 365 (Plan A4) for Faculty",
    "ENTERPRISEPACK_B_PILOT": "Office 365 (Enterprise Preview)",
    "STANDARD_B_PILOT": "Office 365 (Small Business Preview)",
    "VISIOCLIENT": "Visio Pro Online",
    "POWER_BI_ADDON": "Office 365 Power BI Addon",
    "POWER_BI_INDIVIDUAL_USE": "Power BI Individual User",
    "POWER_BI_STANDALONE": "Power BI Stand Alone",
    "POWER_BI_STANDARD": "Power-BI Standard",
    "PROJECTESSENTIALS": "Project Lite",
    "PROJECTCLIENT": "Project Professional",
    "PROJECTONLINE_PLAN_1": "Project Online",
    "PROJECTONLINE_PLAN_2": "Project Online and PRO",
    "ProjectPremium": "Project Online Premium",
    "ECAL_SERVICES": "ECAL",
    "EMS": "Enterprise Mobility Suite",
    "RIGHTSMANAGEMENT_ADHOC": "Windows Azure Rights Management",
    "MCOMEETADV": "PSTN conferencing",
    "SHAREPOINTSTORAGE": "SharePoint storage",
    "PLANNERSTANDALONE": "Planner Standalone",
    "CRMIUR": "CMRIUR",
    "BI_AZURE_P1": "Power BI Reporting and Analytics",
    "INTUNE_A": "Windows Intune Plan A",
    "PROJECTWORKMANAGEMENT": "Office 365 Planner Preview",
    "ATP_ENTERPRISE": "Exchange Online Advanced Threat Protection",
    "EQUIVIO_ANALYTICS": "Office 365 Advanced eDiscovery",
    "AAD_BASIC": "Azure Active Directory Basic",
    "RMS_S_ENTERPRISE": "Azure Active Directory Rights Management",
    "AAD_PREMIUM": "Azure Active Directory Premium",
    "MFA_PREMIUM": "Azure Multi-Factor Authentication",
    "STANDARDPACK_GOV": "Microsoft Office 365 (Plan G1) for Government",
    "STANDARDWOFFPACK_GOV": "Microsoft Office 365 (Plan G2) for Government",
    "ENTERPRISEPACK_GOV": "Microsoft Office 365 (Plan G3) for Government",
    "ENTERPRISEWITHSCAL_GOV": "Microsoft Office 365 (Plan G4) for Government",
    "DESKLESSPACK_GOV": "Microsoft Office 365 (Plan K1) for Government",
    "ESKLESSWOFFPACK_GOV": "Microsoft Office 365 (Plan K2) for Government",
    "EXCHANGESTANDARD_GOV": "Microsoft Office 365 Exchange Online (Plan 1) only for Government",
    "EXCHANGEENTERPRISE_GOV": "Microsoft Office 365 Exchange Online (Plan 2) only for Government",
    "SHAREPOINTDESKLESS_GOV": "SharePoint Online Kiosk",
    "EXCHANGE_S_DESKLESS_GOV": "Exchange Kiosk",
    "RMS_S_ENTERPRISE_GOV": "Windows Azure Active Directory Rights Management",
    "OFFICESUBSCRIPTION_GOV": "Office ProPlus",
    "MCOSTANDARD_GOV": "Lync Plan 2G",
    "SHAREPOINTWAC_GOV": "Office Online for Government",
    "SHAREPOINTENTERPRISE_GOV": "SharePoint Plan 2G",
    "EXCHANGE_S_ENTERPRISE_GOV": "Exchange Plan 2G",
    "EXCHANGE_S_ARCHIVE_ADDON_GOV": "Exchange Online Archiving",
    "EXCHANGE_S_DESKLESS": "Exchange Online Kiosk",
    "SHAREPOINTDESKLESS": "SharePoint Online Kiosk",
    "SHAREPOINTWAC": "Office Online",
    "YAMMER_ENTERPRISE": "Yammer Enterprise",
    "EXCHANGE_L_STANDARD": "Exchange Online (Plan 1)",
    "MCOLITE": "Lync Online (Plan 1)",
    "SHAREPOINTLITE": "SharePoint Online (Plan 1)",
    "OFFICE_PRO_PLUS_SUBSCRIPTION_SMBIZ": "Office ProPlus",
    "EXCHANGE_S_STANDARD_MIDMARKET": "Exchange Online (Plan 1)",
    "MCOSTANDARD_MIDMARKET": "Lync Online (Plan 1)",
    "SHAREPOINTENTERPRISE_MIDMARKET": "SharePoint Online (Plan 1)",
    "OFFICESUBSCRIPTION": "Office ProPlus",
    "YAMMER_MIDSIZE": "Yammer",
    "DYN365_ENTERPRISE_PLAN1": "Dynamics 365 Customer Engagement Plan Enterprise Edition",
    "ENTERPRISEPREMIUM_NOPSTNCONF": "Enterprise E5 (without Audio Conferencing)",
    "ENTERPRISEPREMIUM": "Enterprise E5 (with Audio Conferencing)",
    "MCOSTANDARD": "Skype for Business Online Standalone Plan 2",
    "PROJECT_MADEIRA_PREVIEW_IW_SKU": "Dynamics 365 for Financials for IWs",
    "STANDARDWOFFPACK_IW_STUDENT": "Office 365 Education for Students",
    "STANDARDWOFFPACK_IW_FACULTY": "Office 365 Education for Faculty",
    "EOP_ENTERPRISE_FACULTY": "Exchange Online Protection for Faculty",
    "EXCHANGESTANDARD_STUDENT": "Exchange Online (Plan 1) for Students",
    "OFFICESUBSCRIPTION_STUDENT": "Office ProPlus Student Benefit",
    "STANDARDWOFFPACK_FACULTY": "Office 365 Education E1 for Faculty",
    "STANDARDWOFFPACK_STUDENT": "Microsoft Office 365 (Plan A2) for Students",
    "DYN365_FINANCIALS_BUSINESS_SKU": "Dynamics 365 for Financials Business Edition",
    "DYN365_FINANCIALS_TEAM_MEMBERS_SKU": "Dynamics 365 for Team Members Business Edition",
    "FLOW_FREE": "Microsoft Flow Free",
    "POWER_BI_PRO": "Power BI Pro",
    "O365_BUSINESS": "Office 365 Business",
    "DYN365_ENTERPRISE_SALES": "Dynamics Office 365 Enterprise Sales",
    "RIGHTSMANAGEMENT": "Rights Management",
    "PROJECTPROFESSIONAL": "Project Professional",
    "VISIOONLINE_PLAN1": "Visio Online Plan 1",
    "EXCHANGEENTERPRISE": "Exchange Online Plan 2",
    "DYN365_ENTERPRISE_P1_IW": "Dynamics 365 P1 Trial for Information Workers",
    "DYN365_ENTERPRISE_TEAM_MEMBERS": "Dynamics 365 For Team Members Enterprise Edition",
    "CRMSTANDARD": "Microsoft Dynamics CRM Online Professional",
    "EXCHANGEARCHIVE_ADDON": "Exchange Online Archiving For Exchange Online",
    "EXCHANGEDESKLESS": "Exchange Online Kiosk",
    "SPZA_IW": "App Connect",
    "WINDOWS_STORE": "Windows Store for Business",
    "MCOEV": "Microsoft Phone System",
    "VIDEO_INTEROP": "Polycom Skype Meeting Video Interop for Skype for Business",
    "SPE_E5": "Microsoft 365 E5",
    "SPE_E3": "Microsoft 365 E3",
    "ATA": "Advanced Threat Analytics",
    "MCOPSTN2": "Domestic and International Calling Plan",
    "FLOW_P1": "Microsoft Flow Plan 1",
    "FLOW_P2": "Microsoft Flow Plan 2",
    "CRMSTORAGE": "Microsoft Dynamics CRM Online Additional Storage",
    "SMB_APPS": "Microsoft Business Apps",
    "MICROSOFT_BUSINESS_CENTER": "Microsoft Business Center",
    "DYN365_TEAM_MEMBERS": "Dynamics 365 Team Members",
    "STREAM": "Microsoft Stream Trial",
    "EMSPREMIUM": "ENTERPRISE MOBILITY + SECURITY E5",
}


def _normalise_license(row: dict[str, Any]) -> dict[str, Any]:
    normalised = dict(row)
    platform = str(normalised.get("platform") or "").strip()
    friendly_name = _MICROSOFT_LICENSE_FRIENDLY_NAMES.get(platform)
    if friendly_name:
        normalised["display_name"] = friendly_name
    if "company_id" in normalised and normalised["company_id"] is not None:
        normalised["company_id"] = int(normalised["company_id"])
    if "count" in normalised and normalised["count"] is not None:
        normalised["count"] = int(normalised["count"])
    if "allocated" in normalised and normalised["allocated"] is not None:
        normalised["allocated"] = int(normalised["allocated"])
    for key in ("expiry_date", "token_expires_at"):
        value = normalised.get(key)
        if isinstance(value, datetime):
            normalised[key] = value.replace(tzinfo=None)
    return normalised


_ALLOCATED_SUBQUERY = """
    (SELECT COUNT(DISTINCT s.id)
     FROM staff AS s
     WHERE s.id IN (
         SELECT sl2.staff_id FROM staff_licenses AS sl2 WHERE sl2.license_id = l.id
         UNION
         SELECT ogm.staff_id
         FROM group_licenses AS gl
         INNER JOIN office_group_members AS ogm ON ogm.group_id = gl.group_id
         WHERE gl.license_id = l.id
     )
    )
"""


async def list_company_licenses(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        f"""
        SELECT l.*, COALESCE(a.name, l.name) AS display_name,
               {_ALLOCATED_SUBQUERY} AS allocated
        FROM licenses AS l
        LEFT JOIN apps AS a ON a.vendor_sku = l.platform
        WHERE l.company_id = %s
        GROUP BY l.id
        ORDER BY display_name, l.name
        """,
        (company_id,),
    )
    return [_normalise_license(row) for row in rows]


async def list_all_licenses() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        f"""
        SELECT l.*, COALESCE(a.name, l.name) AS display_name,
               {_ALLOCATED_SUBQUERY} AS allocated
        FROM licenses AS l
        LEFT JOIN apps AS a ON a.vendor_sku = l.platform
        GROUP BY l.id
        ORDER BY l.company_id, display_name
        """,
    )
    return [_normalise_license(row) for row in rows]


async def get_license_by_id(license_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        f"""
        SELECT l.*, COALESCE(a.name, l.name) AS display_name,
               {_ALLOCATED_SUBQUERY} AS allocated
        FROM licenses AS l
        LEFT JOIN apps AS a ON a.vendor_sku = l.platform
        WHERE l.id = %s
        GROUP BY l.id
        """,
        (license_id,),
    )
    return _normalise_license(row) if row else None


async def get_license_by_company_and_sku(company_id: int, sku: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        f"""
        SELECT l.*, COALESCE(a.name, l.name) AS display_name,
               {_ALLOCATED_SUBQUERY} AS allocated
        FROM licenses AS l
        LEFT JOIN apps AS a ON a.vendor_sku = l.platform
        WHERE l.company_id = %s AND l.platform = %s
        GROUP BY l.id
        """,
        (company_id, sku),
    )
    return _normalise_license(row) if row else None


async def create_license(
    *,
    company_id: int,
    name: str,
    platform: str,
    count: int,
    expiry_date: datetime | None,
    contract_term: str | None,
) -> dict[str, Any]:
    license_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO licenses (company_id, name, platform, count, expiry_date, contract_term)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            company_id,
            name,
            platform,
            count,
            expiry_date,
            contract_term,
        ),
    )
    if not license_id:
        raise RuntimeError("Failed to create license")
    row = await db.fetch_one("SELECT * FROM licenses WHERE id = %s", (license_id,))
    if not row:
        raise RuntimeError("Failed to retrieve created license")
    return _normalise_license(row)


async def update_license(
    license_id: int,
    *,
    company_id: int,
    name: str,
    platform: str,
    count: int,
    expiry_date: datetime | None,
    contract_term: str | None,
) -> dict[str, Any]:
    await db.execute(
        """
        UPDATE licenses
        SET company_id = %s, name = %s, platform = %s, count = %s, expiry_date = %s, contract_term = %s
        WHERE id = %s
        """,
        (
            company_id,
            name,
            platform,
            count,
            expiry_date,
            contract_term,
            license_id,
        ),
    )
    updated = await get_license_by_id(license_id)
    if not updated:
        raise ValueError("License not found after update")
    return updated


async def delete_license(license_id: int) -> None:
    await db.execute("DELETE FROM staff_licenses WHERE license_id = %s", (license_id,))
    await db.execute("DELETE FROM licenses WHERE id = %s", (license_id,))


async def list_staff_for_license(license_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT DISTINCT s.id, s.first_name, s.last_name, s.email
        FROM staff AS s
        WHERE s.id IN (
            SELECT sl.staff_id FROM staff_licenses AS sl WHERE sl.license_id = %s
            UNION
            SELECT ogm.staff_id
            FROM group_licenses AS gl
            INNER JOIN office_group_members AS ogm ON ogm.group_id = gl.group_id
            WHERE gl.license_id = %s
        )
        ORDER BY s.last_name, s.first_name
        """,
        (license_id, license_id),
    )
    return [dict(row) for row in rows]


async def list_groups_for_license(license_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT og.id, og.name
        FROM group_licenses AS gl
        INNER JOIN office_groups AS og ON og.id = gl.group_id
        WHERE gl.license_id = %s
        ORDER BY og.name
        """,
        (license_id,),
    )
    return [dict(row) for row in rows]


async def link_group_to_license(group_id: int, license_id: int) -> None:
    await db.execute(
        """
        INSERT IGNORE INTO group_licenses (group_id, license_id)
        VALUES (%s, %s)
        """,
        (group_id, license_id),
    )


async def unlink_group_from_license(group_id: int, license_id: int) -> None:
    await db.execute(
        "DELETE FROM group_licenses WHERE group_id = %s AND license_id = %s",
        (group_id, license_id),
    )


async def link_staff_to_license(staff_id: int, license_id: int) -> None:
    await db.execute(
        """
        INSERT INTO staff_licenses (staff_id, license_id)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE staff_id = VALUES(staff_id)
        """,
        (staff_id, license_id),
    )


async def unlink_staff_from_license(staff_id: int, license_id: int) -> None:
    await db.execute(
        "DELETE FROM staff_licenses WHERE staff_id = %s AND license_id = %s",
        (staff_id, license_id),
    )


async def bulk_unlink_staff(license_id: int, staff_ids: Iterable[int]) -> None:
    ids = list(staff_ids)
    if not ids:
        return
    placeholders = ", ".join(["%s"] * len(ids))
    await db.execute(
        f"DELETE FROM staff_licenses WHERE license_id = %s AND staff_id IN ({placeholders})",
        tuple([license_id, *ids]),
    )
