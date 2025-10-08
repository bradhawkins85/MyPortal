from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.logging import log_info
from app.repositories import companies as company_repo
from app.repositories import staff as staff_repo
from app.services import syncro


@dataclass(slots=True)
class ImportSummary:
    company_id: int
    created: int
    updated: int
    skipped: int

    @property
    def total(self) -> int:
        return self.created + self.updated + self.skipped


def _normalise(value: str | None) -> str:
    return value.strip() if value else ""


def _find_existing_staff(
    existing_staff: list[dict[str, Any]],
    *,
    first_name: str,
    last_name: str,
    email: str | None,
) -> dict[str, Any] | None:
    email_lower = email.lower() if email else None
    for member in existing_staff:
        member_email = member.get("email")
        if email_lower and member_email and member_email.lower() == email_lower:
            return member
        member_first = member.get("first_name", "").lower()
        member_last = member.get("last_name", "").lower()
        if (
            not email_lower
            and member_first == first_name.lower()
            and member_last == last_name.lower()
        ):
            return member
    return None


async def import_contacts_for_company(
    company_id: int,
    *,
    syncro_company_id: str | None = None,
) -> ImportSummary:
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise ValueError(f"Company {company_id} not found")
    syncro_id = syncro_company_id or company.get("syncro_company_id")
    if not syncro_id:
        raise syncro.SyncroConfigurationError("Company is missing a Syncro mapping")

    log_info("Starting Syncro contact import", company_id=company_id, syncro_id=syncro_id)
    contacts = await syncro.get_contacts(syncro_id)
    existing_staff = await staff_repo.list_staff(company_id)

    created = 0
    updated = 0
    skipped = 0

    for contact in contacts:
        full_name = " ".join(
            part
            for part in (
                _normalise(contact.get("first_name")),
                _normalise(contact.get("last_name")),
            )
            if part
        ).strip()
        if not full_name and contact.get("name"):
            full_name = _normalise(contact.get("name"))
        if not full_name:
            skipped += 1
            continue
        if "ex staff" in full_name.lower():
            skipped += 1
            continue

        parts = full_name.split()
        first_name = _normalise(contact.get("first_name") or (parts[0] if parts else ""))
        last_name = _normalise(
            contact.get("last_name")
            or (" ".join(parts[1:]) if len(parts) > 1 else "")
        )
        email = _normalise(contact.get("email") or contact.get("email_address") or None)
        email = email or None
        phone = _normalise(contact.get("mobile") or contact.get("phone") or None) or None

        existing = _find_existing_staff(
            existing_staff,
            first_name=first_name or "Unknown",
            last_name=last_name or "",
            email=email,
        )

        address = _normalise(contact.get("address1") or contact.get("address") or None)
        city = _normalise(contact.get("city") or None) or None
        state = _normalise(contact.get("state") or None) or None
        postcode = _normalise(contact.get("zip") or None) or None
        country = _normalise(contact.get("country") or None) or None
        department = _normalise(contact.get("department") or None) or None
        job_title = _normalise(contact.get("title") or None) or None

        if existing:
            await staff_repo.update_staff(
                existing["id"],
                company_id=company_id,
                first_name=first_name or existing.get("first_name", ""),
                last_name=last_name or existing.get("last_name", ""),
                email=email or existing.get("email", ""),
                mobile_phone=phone or existing.get("mobile_phone"),
                date_onboarded=existing.get("date_onboarded"),
                date_offboarded=existing.get("date_offboarded"),
                enabled=bool(existing.get("enabled", True)),
                street=address or existing.get("street"),
                city=city or existing.get("city"),
                state=state or existing.get("state"),
                postcode=postcode or existing.get("postcode"),
                country=country or existing.get("country"),
                department=department or existing.get("department"),
                job_title=job_title or existing.get("job_title"),
                org_company=existing.get("org_company"),
                manager_name=existing.get("manager_name"),
                account_action=existing.get("account_action"),
                syncro_contact_id=str(contact.get("id")) if contact.get("id") else existing.get("syncro_contact_id"),
            )
            updated += 1
        else:
            created_staff = await staff_repo.create_staff(
                company_id=company_id,
                first_name=first_name or "Unknown",
                last_name=last_name or last_name or "",
                email=email or "",
                mobile_phone=phone,
                date_onboarded=None,
                date_offboarded=None,
                enabled=True,
                street=address or None,
                city=city,
                state=state,
                postcode=postcode,
                country=country,
                department=department,
                job_title=job_title,
                org_company=None,
                manager_name=None,
                account_action=None,
                syncro_contact_id=str(contact.get("id")) if contact.get("id") else None,
            )
            existing_staff.append(created_staff)
            created += 1

    log_info(
        "Completed Syncro contact import",
        company_id=company_id,
        created=created,
        updated=updated,
        skipped=skipped,
    )
    return ImportSummary(company_id=company_id, created=created, updated=updated, skipped=skipped)


async def import_contacts_for_syncro_id(syncro_company_id: str) -> ImportSummary:
    company = await company_repo.get_company_by_syncro_id(syncro_company_id)
    if not company:
        raise ValueError("Company not found for supplied Syncro identifier")
    return await import_contacts_for_company(company["id"], syncro_company_id=syncro_company_id)
