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
from app.repositories import staff_custom_fields as staff_custom_fields_repo
from app.services import value_templates
from app.services.m365_signature_import import sanitize_signature_html
from app.services.sanitization import SanitizedRichText, sanitize_rich_text

_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,118}[a-z0-9])?$")
_TOKEN_PATTERN = re.compile(r"\{\{\s*([^\s{}]+)\s*\}\}")
_CUSTOM_FIELD_PATTERN = re.compile(r"^custom\.[A-Za-z0-9_.-]{1,100}$")

SIGNATURE_ROLES = ("primary", "additional")
TARGETING_MATCHES = ("all", "any")
TARGETING_OPERATORS = {
    "in": "is one of",
    "not_in": "is not one of",
    "contains": "contains",
    "not_contains": "does not contain",
}
# Staff attributes a signature can be targeted on. Custom staff fields are
# added per company as ``custom.<name>``.
TARGETING_FIELDS = {
    "email": "Staff member (email)",
    "email_domain": "Email domain",
    "job_title": "Job title",
    "department": "Department",
    "city": "Site / city",
    "state": "State",
    "country": "Country",
    "org_company": "Organisation",
    "manager_name": "Manager",
}
MAX_TARGETING_RULES = 20
MAX_TARGETING_VALUES = 100
_MAX_TARGETING_VALUE_LENGTH = 255


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


def _normalise_role(role: Any) -> str:
    candidate = str(role or "primary").strip().lower()
    return candidate if candidate in SIGNATURE_ROLES else "primary"


def _normalise_match(match: Any) -> str:
    candidate = str(match or "all").strip().lower()
    return candidate if candidate in TARGETING_MATCHES else "all"


def _split_values(values: Any) -> list[str]:
    if isinstance(values, str):
        raw = re.split(r"[\n,;]", values)
    elif isinstance(values, (list, tuple)):
        raw = [str(item) for item in values]
    else:
        raw = []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = item.strip()[:_MAX_TARGETING_VALUE_LENGTH]
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            cleaned.append(value)
    return cleaned


def normalise_targeting_rules(rules: Any) -> list[dict[str, Any]]:
    """Validate targeting rules, dropping rows that have no values.

    Each rule is ``{"field": ..., "operator": ..., "values": [...]}``.  Raises
    ``ValueError`` for an unknown field or operator or too many rules/values.
    """
    if not rules:
        return []
    if not isinstance(rules, (list, tuple)):
        raise ValueError("Targeting conditions must be a list")
    cleaned: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        values = _split_values(rule.get("values"))
        if not values:
            continue
        field = str(rule.get("field") or "").strip()
        if field not in TARGETING_FIELDS and not _CUSTOM_FIELD_PATTERN.fullmatch(field):
            raise ValueError(f"Unknown targeting field: {field or '(blank)'}")
        operator = str(rule.get("operator") or "in").strip().lower()
        if operator not in TARGETING_OPERATORS:
            raise ValueError(f"Unknown targeting operator: {operator}")
        if len(values) > MAX_TARGETING_VALUES:
            raise ValueError(f"Each condition can list at most {MAX_TARGETING_VALUES} values")
        cleaned.append({"field": field, "operator": operator, "values": values})
    if len(cleaned) > MAX_TARGETING_RULES:
        raise ValueError(f"Use at most {MAX_TARGETING_RULES} targeting conditions")
    return cleaned


def _staff_values(staff: dict[str, Any], field: str) -> list[str]:
    if field == "email_domain":
        _, separator, domain = str(staff.get("email") or "").rpartition("@")
        raw: Any = domain if separator else ""
    elif field.startswith("custom."):
        raw = dict(staff.get("custom_fields") or {}).get(field[len("custom."):])
    else:
        raw = staff.get(field)
    if raw is None:
        return []
    if isinstance(raw, bool):
        return ["true", "yes"] if raw else ["false", "no"]
    if isinstance(raw, (list, tuple, set)):
        items = [str(item) for item in raw]
    else:
        items = [str(raw)]
        if field.startswith("custom.") and "," in items[0]:
            # Multi-select custom fields are stored as comma-separated text.
            items.extend(items[0].split(","))
    return [item.strip().casefold() for item in items if item and item.strip()]


def _rule_matches(rule: dict[str, Any], staff: dict[str, Any]) -> bool:
    staff_values = _staff_values(staff, str(rule.get("field") or ""))
    wanted = [value.casefold() for value in _split_values(rule.get("values"))]
    operator = rule.get("operator")
    if operator in ("contains", "not_contains"):
        found = any(term in value for value in staff_values for term in wanted)
        return found if operator == "contains" else not found
    found = any(value in wanted for value in staff_values)
    return not found if operator == "not_in" else found


def is_targeted(template: dict[str, Any]) -> bool:
    return bool(template.get("targeting_rules"))


def template_applies_to_staff(template: dict[str, Any], staff: dict[str, Any] | None) -> bool:
    """Whether the template's targeting conditions include this staff member.

    A template without conditions applies to everyone.  Without a staff
    record only untargeted templates apply.
    """
    rules = template.get("targeting_rules") or []
    if not rules:
        return True
    if not staff:
        return False
    results = (_rule_matches(rule, staff) for rule in rules if isinstance(rule, dict))
    if _normalise_match(template.get("targeting_match")) == "any":
        return any(results)
    return all(results)


def describe_targeting(template: dict[str, Any]) -> str:
    rules = template.get("targeting_rules") or []
    if not rules:
        return "Everyone"
    joiner = " or " if _normalise_match(template.get("targeting_match")) == "any" else " and "
    parts = []
    for rule in rules:
        field = str(rule.get("field") or "")
        label = TARGETING_FIELDS.get(field) or field.removeprefix("custom.").replace("_", " ")
        operator = TARGETING_OPERATORS.get(str(rule.get("operator") or ""), "is one of")
        parts.append(f"{label} {operator} {', '.join(_split_values(rule.get('values')))}")
    return joiner.join(parts)


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


def _rank(template: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        _normalise_priority(template.get("priority")),
        1 if template.get("is_default") else 0,
        -((_normalise_date(template.get("schedule_start_on")) or date.max).toordinal()),
        -int(template.get("id") or 0),
    )


def pick_primary_template(
    templates: list[dict[str, Any]],
    *,
    on_date: date | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Pick the primary signature for a staff member.

    Only active, primary-role templates whose targeting includes the staff
    member are candidates; without ``staff`` only templates for everyone are.
    """
    return resolve_staff_signatures(templates, staff=staff, on_date=on_date)["primary"]


def resolve_staff_signatures(
    templates: list[dict[str, Any]],
    *,
    staff: dict[str, Any] | None = None,
    on_date: date | None = None,
) -> dict[str, Any]:
    """Return ``{"primary": template | None, "additional": [templates]}``.

    Every active template that applies to the staff member is available to
    them; the highest ranked primary-role template is their primary signature
    and the rest are additional signatures, highest ranked first.
    """
    effective_date = on_date or current_schedule_date()
    applicable = sorted(
        (
            template
            for template in templates
            if is_template_active(template, on_date=effective_date)
            and template_applies_to_staff(template, staff)
        ),
        key=_rank,
        reverse=True,
    )
    primary = next(
        (template for template in applicable if _normalise_role(template.get("signature_role")) == "primary"),
        None,
    )
    additional = [template for template in applicable if template is not primary]
    return {"primary": primary, "additional": additional}


def _sanitise_signature(html_content: str | None) -> SanitizedRichText:
    """Sanitise signature HTML while keeping inline styles and embedded images.

    The generic rich-text sanitiser strips ``style`` attributes, which would
    destroy the layout of imported Outlook signatures on every save.
    """
    raw = str(html_content or "").strip()
    if raw and "<" not in raw and ">" not in raw:
        raw = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br />")
    cleaned = sanitize_signature_html(raw)
    text_content = sanitize_rich_text(cleaned).text_content
    has_media = bool(re.search(r"<img\b[^>]*\bsrc=", cleaned, flags=re.IGNORECASE))
    if not text_content and not has_media:
        cleaned = ""
    return SanitizedRichText(
        html=cleaned, text_content=text_content, has_rich_content=bool(text_content) or has_media
    )


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
    signature_role: Any = "primary",
    targeting_match: Any = "all",
    targeting_rules: Any = None,
    user_id: int | None,
    skip_sanitization: bool = False,
) -> dict[str, Any]:
    normalised_slug = _normalise_slug(slug)
    existing = await signatures_repo.get_template_by_slug(company_id, normalised_slug)
    if existing:
        raise ValueError("A signature template with this slug already exists")
    if skip_sanitization:
        # Content was already sanitised upstream (e.g. Outlook signature import).
        sanitised = type("S", (), {"html": html_content, "text": text_content or ""})()
    else:
        sanitised = _sanitise_signature(html_content)
    final_text = str(text_content or "").strip() or generate_initial_text(sanitised.html)
    parsed_start_on = _normalise_date(schedule_start_on)
    parsed_end_on = _normalise_date(schedule_end_on)
    _validate_schedule_dates(parsed_start_on, parsed_end_on)
    rules = normalise_targeting_rules(targeting_rules)
    role = _normalise_role(signature_role)
    # Only a primary-role template can be the fallback primary signature.
    is_default = bool(is_default) and role == "primary"
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
        signature_role=role,
        targeting_match=_normalise_match(targeting_match),
        targeting_rules=rules,
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
    signature_role: Any = "primary",
    targeting_match: Any = "all",
    targeting_rules: Any = None,
    user_id: int | None,
) -> dict[str, Any] | None:
    current = await get_template(company_id, template_id)
    if not current:
        return None
    normalised_slug = _normalise_slug(slug)
    existing = await signatures_repo.get_template_by_slug(company_id, normalised_slug)
    if existing and int(existing["id"]) != int(template_id):
        raise ValueError("A signature template with this slug already exists")
    sanitised = _sanitise_signature(html_content)
    final_text = str(text_content or "").strip() or generate_initial_text(sanitised.html)
    parsed_start_on = _normalise_date(schedule_start_on)
    parsed_end_on = _normalise_date(schedule_end_on)
    _validate_schedule_dates(parsed_start_on, parsed_end_on)
    rules = normalise_targeting_rules(targeting_rules)
    role = _normalise_role(signature_role)
    # Only a primary-role template can be the fallback primary signature.
    is_default = bool(is_default) and role == "primary"
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
        signature_role=role,
        targeting_match=_normalise_match(targeting_match),
        targeting_rules=rules,
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
        signature_role=_normalise_role(source.get("signature_role")),
        targeting_match=_normalise_match(source.get("targeting_match")),
        targeting_rules=list(source.get("targeting_rules") or []),
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
    sanitised_html = _sanitise_signature(rendered_html)
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
    """Primary signature for staff not covered by any targeted template."""
    templates = await list_templates(company_id)
    return pick_primary_template(templates, on_date=current_schedule_date(now=now))


async def with_custom_fields(company_id: int, staff: dict[str, Any]) -> dict[str, Any]:
    """Return the staff record with ``custom_fields`` loaded for targeting."""
    if "custom_fields" in staff or staff.get("id") is None:
        return staff
    values = await staff_custom_fields_repo.get_all_staff_field_values(company_id, [int(staff["id"])])
    return {**staff, "custom_fields": values.get(int(staff["id"]), {})}


async def list_targeting_fields(company_id: int) -> list[dict[str, Any]]:
    """Fields offered by the targeting editor, with value suggestions."""
    staff_rows = await staff_repo.list_staff(company_id, enabled=True, exclude_ex_staff=True, page_size=500)
    domains = await companies_repo.get_email_domains_for_company(company_id)
    definitions = await staff_custom_fields_repo.list_field_definitions(company_id)

    def _distinct(values: list[Any]) -> list[str]:
        seen: dict[str, str] = {}
        for value in values:
            text = str(value or "").strip()
            if text and text.casefold() not in seen:
                seen[text.casefold()] = text
        return sorted(seen.values(), key=str.casefold)

    fields: list[dict[str, Any]] = []
    for key, label in TARGETING_FIELDS.items():
        if key == "email_domain":
            suggestions = _distinct(
                [*domains, *(str(row.get("email") or "").rpartition("@")[2] for row in staff_rows)]
            )
        else:
            suggestions = _distinct([row.get(key) for row in staff_rows])
        fields.append({"key": key, "label": label, "suggestions": suggestions})
    for definition in definitions:
        name = str(definition.get("name") or "").strip()
        key = f"custom.{name}"
        if not name or not _CUSTOM_FIELD_PATTERN.fullmatch(key):
            continue
        options = [option.get("value") for option in definition.get("options") or []]
        values: list[Any] = list(options)
        for row in staff_rows:
            value = dict(row.get("custom_fields") or {}).get(name)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                values.append(value)
        fields.append(
            {
                "key": key,
                "label": str(definition.get("display_name") or name).strip(),
                "suggestions": _distinct(values)[:500],
            }
        )
    return fields


__all__ = [
    "SIGNATURE_ROLES",
    "TARGETING_FIELDS",
    "TARGETING_OPERATORS",
    "build_preview_context",
    "describe_targeting",
    "is_targeted",
    "list_targeting_fields",
    "normalise_targeting_rules",
    "resolve_staff_signatures",
    "template_applies_to_staff",
    "with_custom_fields",
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
