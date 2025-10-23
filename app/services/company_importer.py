"""Utilities to synchronise companies from Syncro into MyPortal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.logging import log_error, log_info
from app.repositories import companies as company_repo
from app.services import syncro


@dataclass(slots=True)
class CompanyImportSummary:
    """Summarises the results of a Syncro company import."""

    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0

    def record(self, outcome: str) -> None:
        if outcome == "created":
            self.created += 1
        elif outcome == "updated":
            self.updated += 1
        else:
            self.skipped += 1

    def as_dict(self) -> dict[str, int]:
        return {
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
        }


def _coerce_syncro_id(customer: dict[str, Any]) -> str | None:
    for key in ("id", "customer_id", "customerId"):
        if customer.get(key) is None:
            continue
        raw_value = str(customer[key]).strip()
        if raw_value:
            return raw_value
    return None


def _extract_name(customer: dict[str, Any]) -> str | None:
    candidates = (
        customer.get("business_name"),
        customer.get("company_name"),
        customer.get("name"),
    )
    for candidate in candidates:
        if not candidate:
            continue
        text = str(candidate).strip()
        if text:
            return text

    first = str(
        customer.get("first_name")
        or customer.get("firstname")
        or customer.get("primary_contact_first_name")
        or ""
    ).strip()
    last = str(
        customer.get("last_name")
        or customer.get("lastname")
        or customer.get("primary_contact_last_name")
        or ""
    ).strip()
    joined = " ".join(part for part in (first, last) if part)
    if joined:
        return joined

    contact = customer.get("primary_contact") or customer.get("contact_name")
    if contact:
        text = str(contact).strip()
        if text:
            return text
    return None


def _normalise_address_part(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_address(customer: dict[str, Any]) -> str | None:
    parts: list[str] = []
    candidate_keys = (
        "business_address",
        "address",
        "address1",
        "address_1",
        "street",
        "address2",
        "address_2",
        "city",
        "state",
        "province",
        "zip",
        "zipcode",
        "postal_code",
        "country",
    )

    for key in candidate_keys:
        value = customer.get(key)
        if isinstance(value, dict):
            nested_parts = [
                _normalise_address_part(v)
                for v in (
                    value.get("line1"),
                    value.get("line2"),
                    value.get("city"),
                    value.get("state"),
                    value.get("postal_code"),
                    value.get("country"),
                )
            ]
            for part in nested_parts:
                if part and part not in parts:
                    parts.append(part)
            continue

        part = _normalise_address_part(value)
        if part and part not in parts:
            parts.append(part)

    location = customer.get("location")
    if isinstance(location, dict):
        for key in ("address1", "city", "state", "zip", "country"):
            part = _normalise_address_part(location.get(key))
            if part and part not in parts:
                parts.append(part)

    if not parts:
        return None
    return ", ".join(parts)


def _should_update(existing_value: Any, new_value: str | None) -> bool:
    if new_value is None:
        return False
    existing_text = str(existing_value or "").strip()
    return existing_text != new_value.strip()


async def _upsert_company(customer: dict[str, Any]) -> str:
    if not isinstance(customer, dict):
        return "skipped"

    syncro_id = _coerce_syncro_id(customer)
    name = _extract_name(customer)
    if not syncro_id or not name:
        return "skipped"

    address = _extract_address(customer)

    existing = await company_repo.get_company_by_syncro_id(syncro_id)
    if not existing:
        existing = await company_repo.get_company_by_name(name)

    if existing:
        updates: dict[str, Any] = {}
        if _should_update(existing.get("name"), name):
            updates["name"] = name
        if _should_update(existing.get("address"), address):
            updates["address"] = address
        if _should_update(existing.get("syncro_company_id"), syncro_id):
            updates["syncro_company_id"] = syncro_id
        if not updates:
            return "skipped"
        await company_repo.update_company(int(existing["id"]), **updates)
        return "updated"

    payload = {"name": name, "syncro_company_id": syncro_id}
    if address:
        payload["address"] = address
    await company_repo.create_company(**payload)
    return "created"


def _extract_total_pages(meta: dict[str, Any] | None) -> int | None:
    if not isinstance(meta, dict):
        return None
    candidates = (
        meta.get("total_pages"),
        meta.get("totalPages"),
        meta.get("total"),
    )
    for candidate in candidates:
        try:
            if candidate is None:
                continue
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


async def import_all_companies(
    *, rate_limiter: syncro.AsyncRateLimiter | None = None, per_page: int = 200
) -> CompanyImportSummary:
    summary = CompanyImportSummary()
    limiter = rate_limiter or await syncro.get_rate_limiter()
    log_info("Starting Syncro company import", per_page=per_page)

    page = 1
    total_pages: int | None = None

    while True:
        try:
            customers, meta = await syncro.list_customers(
                page=page, per_page=per_page, rate_limiter=limiter
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error("Failed to fetch Syncro customers", page=page, error=str(exc))
            raise

        if not customers:
            break

        summary.fetched += len(customers)
        for customer in customers:
            try:
                outcome = await _upsert_company(customer)
            except Exception as exc:  # pragma: no cover - defensive logging
                log_error(
                    "Failed to import Syncro customer",
                    syncro_id=_coerce_syncro_id(customer),
                    error=str(exc),
                )
                summary.skipped += 1
                continue
            summary.record(outcome)

        if total_pages is None:
            total_pages = _extract_total_pages(meta)
        if total_pages is not None and page >= total_pages:
            break
        page += 1

    log_info(
        "Completed Syncro company import",
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        skipped=summary.skipped,
    )
    return summary

