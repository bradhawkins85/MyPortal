from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.repositories import companies as companies_repo
from app.repositories import company_variables as company_variables_repo
from app.repositories import m365_signatures as signatures_repo
from app.repositories import staff as staff_repo
from app.services import value_templates
from app.services.sanitization import sanitize_rich_text

_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,118}[a-z0-9])?$")
_TOKEN_PATTERN = re.compile(r"\{\{\s*([^\s{}]+)\s*\}\}")


def _normalise_slug(slug: str) -> str:
    candidate = str(slug or "").strip().lower().replace(" ", "-")
    if not _SLUG_PATTERN.fullmatch(candidate):
        raise ValueError(
            "Slug must contain only lowercase letters, numbers, dots, hyphens, or underscores"
        )
    return candidate


def _normalise_status(status: str | None) -> str:
    candidate = str(status or "draft").strip().lower()
    return candidate if candidate in {"draft", "published", "disabled"} else "draft"


def _normalise_priority(priority: Any) -> int:
    try:
        return max(0, int(priority or 0))
    except (TypeError, ValueError):
        return 0


def _normalise_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value.strip())
    raise ValueError("Invalid schedule date")


def _validate_schedule_dates(start_on: date | None, end_on: date | None) -> None:
    if start_on and end_on and start_on > end_on:
        raise ValueError("Schedule start date must be on or before the end date")


def get_schedule_timezone_name() -> str:
    return str(get_settings().default_timezone or "UTC").strip() or "UTC"


def current_schedule_date(*, now: datetime | None = None, timezone_name: str | None = None) -> date:
    active_now = now or datetime.now(timezone.utc)
    if active_now.tzinfo is None:
        active_now = active_now.replace(tzinfo=timezone.utc)
    tz_name = timezone_name or get_schedule_timezone_name()
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc
    return active_now.astimezone(tzinfo).date()


def is_template_active(
    template: dict[str, Any],
    *,
    on_date: date | None = None,
) -> bool:
    if _normalise_status(template.get("status")) != "published":
        return False
    effective_date = on_date or current_schedule_date()
    start_on = _normalise_date(template.get("schedule_start_on"))
    end_on = _normalise_date(template.get("schedule_end_on"))
    if start_on and effective_date < start_on:
        return False
    if end_on and effective_date > end_on:
        return False
    return True


def pick_primary_template(
    templates: list[dict[str, Any]],
    *,
    on_date: date | None = None,
) -> dict[str, Any] | None:
    effective_date = on_date or current_schedule_date()
    active = [template for template in templates if is_template_active(template, on_date=effective_date)]
    if not active:
        return None

    def _sort_key(template: dict[str, Any]) -> tuple[int, int, int, int]:
        return (
            _normalise_priority(template.get("priority")),
            1 if template.get("is_default") else 0,
            -((_normalise_date(template.get("schedule_start_on")) or date.max).toordinal()),
            -int(template.get("id") or 0),
        )

    return max(active, key=_sort_key)


def generate_initial_text(html_content: str | None) -> str:
    cleaned = sanitize_rich_text(html_content)
    lines = [
        line.rstrip()
        for line in cleaned.text_content.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    ]
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        collapsed.append(line)
        previous_blank = blank
    return "\n".join(collapsed).strip()


async def list_templates(company_id: int) -> list[dict[str, Any]]:
    return await signatures_repo.list_templates(company_id)


async def get_template(company_id: int, template_id: int) -> dict[str, Any] | None:
    return await signatures_repo.get_template(company_id, template_id)


async def create_template(
    *,
    company_id: int,
    slug: str,
    name: str,
    description: str | None,
    html_content: str,
    text_content: str | None,
    priority: Any = 0,
    is_default: bool = False,
    schedule_start_on: Any = None,
    schedule_end_on: Any = None,
    user_id: int | None,
) -> dict[str, Any]:
    normalised_slug = _normalise_slug(slug)
    existing = await signatures_repo.get_template_by_slug(company_id, normalised_slug)
    if existing:
        raise ValueError("A signature template with this slug already exists")
    sanitised = sanitize_rich_text(html_content)
    final_text = str(text_content or "").strip() or generate_initial_text(sanitised.html)
    parsed_start_on = _normalise_date(schedule_start_on)
    parsed_end_on = _normalise_date(schedule_end_on)
    _validate_schedule_dates(parsed_start_on, parsed_end_on)
    created = await signatures_repo.create_template(
        company_id=company_id,
        slug=normalised_slug,
        name=str(name or "").strip(),
        description=str(description).strip() if isinstance(description, str) and description.strip() else None,
        html_content=sanitised.html,
        text_content=final_text,
        status="draft",
        priority=_normalise_priority(priority),
        is_default=bool(is_default),
        schedule_start_on=parsed_start_on,
        schedule_end_on=parsed_end_on,
        created_by_user_id=user_id,
        updated_by_user_id=user_id,
    )
    if bool(is_default):
        updated = await signatures_repo.set_default_template(
            company_id,
            int(created["id"]),
            updated_by_user_id=user_id,
        )
        if updated:
            return updated
    return created


async def update_template(
    company_id: int,
    template_id: int,
    *,
    slug: str,
    name: str,
    description: str | None,
    html_content: str,
    text_content: str | None,
    priority: Any = 0,
    is_default: bool = False,
    schedule_start_on: Any = None,
    schedule_end_on: Any = None,
    user_id: int | None,
) -> dict[str, Any] | None:
    current = await get_template(company_id, template_id)
    if not current:
        return None
    normalised_slug = _normalise_slug(slug)
    existing = await signatures_repo.get_template_by_slug(company_id, normalised_slug)
    if existing and int(existing["id"]) != int(template_id):
        raise ValueError("A signature template with this slug already exists")
    sanitised = sanitize_rich_text(html_content)
    final_text = str(text_content or "").strip() or generate_initial_text(sanitised.html)
    parsed_start_on = _normalise_date(schedule_start_on)
    parsed_end_on = _normalise_date(schedule_end_on)
    _validate_schedule_dates(parsed_start_on, parsed_end_on)
    updated = await signatures_repo.update_template(
        company_id,
        template_id,
        slug=normalised_slug,
        name=str(name or "").strip(),
        description=str(description).strip() if isinstance(description, str) and description.strip() else None,
        html_content=sanitised.html,
        text_content=final_text,
        priority=_normalise_priority(priority),
        is_default=bool(is_default),
        schedule_start_on=parsed_start_on,
        schedule_end_on=parsed_end_on,
        updated_by_user_id=user_id,
        disabled_at=None if current.get("status") != "disabled" else current.get("disabled_at"),
    )
    if not updated:
        return None
    if bool(is_default):
        return await signatures_repo.set_default_template(
            company_id,
            template_id,
            updated_by_user_id=user_id,
        )
    return updated


async def clone_template(company_id: int, template_id: int, *, user_id: int | None) -> dict[str, Any] | None:
    source = await get_template(company_id, template_id)
    if not source:
        return None
    base_slug = str(source.get("slug") or "signature")[:110].rstrip("._-") or "signature"
    candidate = f"{base_slug}-copy"
    index = 2
    while await signatures_repo.get_template_by_slug(company_id, candidate):
        candidate = f"{base_slug}-copy-{index}"
        index += 1
    return await signatures_repo.create_template(
        company_id=company_id,
        slug=candidate,
        name=f"{source.get('name') or 'Signature'} (Copy)",
        description=source.get("description"),
        html_content=str(source.get("html_content") or ""),
        text_content=str(source.get("text_content") or ""),
        status="draft",
        priority=_normalise_priority(source.get("priority")),
        is_default=False,
        schedule_start_on=_normalise_date(source.get("schedule_start_on")),
        schedule_end_on=_normalise_date(source.get("schedule_end_on")),
        created_by_user_id=user_id,
        updated_by_user_id=user_id,
    )


async def publish_template(company_id: int, template_id: int, *, user_id: int | None) -> dict[str, Any] | None:
    template = await get_template(company_id, template_id)
    if not template:
        return None
    return await signatures_repo.update_template(
        company_id,
        template_id,
        status="published",
        published_at=datetime.now(timezone.utc),
        disabled_at=None,
        updated_by_user_id=user_id,
    )


async def disable_template(company_id: int, template_id: int, *, user_id: int | None) -> dict[str, Any] | None:
    template = await get_template(company_id, template_id)
    if not template:
        return None
    return await signatures_repo.update_template(
        company_id,
        template_id,
        status="disabled",
        disabled_at=datetime.now(timezone.utc),
        updated_by_user_id=user_id,
    )


async def delete_template(company_id: int, template_id: int) -> bool:
    return await signatures_repo.delete_template(company_id, template_id)


async def build_preview_context(company_id: int, staff_id: int) -> dict[str, Any]:
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff or int(staff.get("company_id") or 0) != int(company_id):
        raise ValueError("Selected staff member was not found for this company")
    company = await companies_repo.get_company_by_id(company_id) or {}
    company_variables = await company_variables_repo.value_map(company_id)
    full_name = " ".join(
        part
        for part in [
            str(staff.get("first_name") or "").strip(),
            str(staff.get("last_name") or "").strip(),
        ]
        if part
    ).strip()
    return {
        "staff": {
            **staff,
            "custom": dict(staff.get("custom_fields") or {}),
        },
        "user": {
            "id": staff.get("id"),
            "email": staff.get("email"),
            "first_name": staff.get("first_name"),
            "firstName": staff.get("first_name"),
            "last_name": staff.get("last_name"),
            "lastName": staff.get("last_name"),
            "full_name": full_name,
            "fullName": full_name,
        },
        "company": {
            **company,
            "variables": company_variables,
        },
        "custom": company_variables,
    }


def _collect_template_tokens(*values: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for value in values:
        for token in _TOKEN_PATTERN.findall(str(value or "")):
            if token not in seen:
                seen.add(token)
                found.append(token)
    return found


async def render_preview(
    company_id: int,
    *,
    html_content: str,
    text_content: str | None,
    staff_id: int,
) -> dict[str, Any]:
    context = await build_preview_context(company_id, staff_id)
    rendered_html = str(await value_templates.render_string_async(html_content or "", context))
    sanitised_html = sanitize_rich_text(rendered_html)
    resolved_text_source = str(text_content or "").strip() or generate_initial_text(sanitised_html.html)
    rendered_text = str(await value_templates.render_string_async(resolved_text_source, context)).strip()
    missing_tokens: list[str] = []
    for token in _collect_template_tokens(html_content, resolved_text_source):
        resolved = await value_templates.render_string_async(f"{{{{{token}}}}}", context)
        if str(resolved or "").strip():
            continue
        if token.startswith("template."):
            continue
        missing_tokens.append(token)
    return {
        "html": sanitised_html.html,
        "text": rendered_text,
        "missing_tokens": missing_tokens,
    }


async def list_preview_staff(company_id: int) -> list[dict[str, Any]]:
    rows = await staff_repo.list_staff(company_id, enabled=True, exclude_ex_staff=True, page_size=200)
    options: list[dict[str, Any]] = []
    for row in rows:
        options.append(
            {
                "id": int(row["id"]),
                "label": " ".join(
                    part for part in [str(row.get("first_name") or "").strip(), str(row.get("last_name") or "").strip()] if part
                ).strip()
                or str(row.get("email") or f"Staff #{row['id']}"),
                "email": str(row.get("email") or "").strip(),
            }
        )
    return options


async def list_variable_suggestions(company_id: int) -> list[str]:
    suggestions = [
        "{{staff.first_name}}",
        "{{staff.last_name}}",
        "{{staff.email}}",
        "{{staff.mobile_phone}}",
        "{{staff.job_title}}",
        "{{staff.department}}",
        "{{user.fullName}}",
        "{{company.name}}",
        "{{APP_PORTAL_URL}}",
        "{{NOW_UTC}}",
    ]
    definitions = await company_variables_repo.list_for_company(company_id)
    for definition in definitions:
        name = str(definition.get("name") or "").strip()
        if name:
            suggestions.append(f"{{{{company.variables.{name}}}}}")
            suggestions.append(f"{{{{custom.{name}}}}}")
    return list(dict.fromkeys(suggestions))


async def get_primary_template(
    company_id: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    templates = await list_templates(company_id)
    return pick_primary_template(templates, on_date=current_schedule_date(now=now))


__all__ = [
    "build_preview_context",
    "clone_template",
    "create_template",
    "delete_template",
    "disable_template",
    "generate_initial_text",
    "get_primary_template",
    "get_schedule_timezone_name",
    "get_template",
    "is_template_active",
    "list_preview_staff",
    "list_templates",
    "list_variable_suggestions",
    "pick_primary_template",
    "publish_template",
    "render_preview",
    "update_template",
]
