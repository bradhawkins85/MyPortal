from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict
from urllib.parse import quote


@dataclass(slots=True)
class TemplateContextUser:
    id: int | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None


@dataclass(slots=True)
class TemplateContextCompany:
    id: int | None = None
    name: str | None = None
    syncro_customer_id: str | None = None


@dataclass(slots=True)
class TemplateContextPortal:
    base_url: str | None = None
    login_url: str | None = None


@dataclass(slots=True)
class TemplateContext:
    user: TemplateContextUser | None = None
    company: TemplateContextCompany | None = None
    portal: TemplateContextPortal | None = None


def _to_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _build_user_replacements(context: TemplateContext) -> Dict[str, str]:
    user = context.user
    if not user:
        return {
            "{{user.email}}": "",
            "{{user.emailUrlEncoded}}": "",
            "{{user.firstName}}": "",
            "{{user.firstNameUrlEncoded}}": "",
            "{{user.lastName}}": "",
            "{{user.lastNameUrlEncoded}}": "",
            "{{user.fullName}}": "",
            "{{user.fullNameUrlEncoded}}": "",
        }

    first = _to_string(user.first_name).strip()
    last = _to_string(user.last_name).strip()
    full_name = " ".join(part for part in (first, last) if part).strip()

    replacements = {
        "{{user.email}}": _to_string(user.email).strip(),
        "{{user.firstName}}": first,
        "{{user.lastName}}": last,
        "{{user.fullName}}": full_name,
    }
    return {
        **replacements,
        **{f"{key}UrlEncoded": quote(value, safe="") for key, value in replacements.items()},
        **{
            "{{user.emailUrlEncoded}}": quote(replacements["{{user.email}}"], safe=""),
            "{{user.firstNameUrlEncoded}}": quote(first, safe=""),
            "{{user.lastNameUrlEncoded}}": quote(last, safe=""),
            "{{user.fullNameUrlEncoded}}": quote(full_name, safe=""),
        },
    }


def _build_company_replacements(context: TemplateContext) -> Dict[str, str]:
    company = context.company
    replacements = {
        "{{company.id}}": "",
        "{{company.idUrlEncoded}}": "",
        "{{company.name}}": "",
        "{{company.nameUrlEncoded}}": "",
        "{{company.syncroId}}": "",
        "{{company.syncroIdUrlEncoded}}": "",
    }
    if not company:
        return replacements

    company_id = ""
    if company.id is not None:
        company_id = str(company.id)
    name = _to_string(company.name).strip()
    syncro_id = _to_string(company.syncro_customer_id).strip()

    replacements.update(
        {
            "{{company.id}}": company_id,
            "{{company.name}}": name,
            "{{company.syncroId}}": syncro_id,
            "{{company.idUrlEncoded}}": quote(company_id, safe=""),
            "{{company.nameUrlEncoded}}": quote(name, safe=""),
            "{{company.syncroIdUrlEncoded}}": quote(syncro_id, safe=""),
        }
    )
    return replacements


def _build_portal_replacements(context: TemplateContext) -> Dict[str, str]:
    portal = context.portal
    replacements = {
        "{{portal.baseUrl}}": "",
        "{{portal.baseUrlUrlEncoded}}": "",
        "{{portal.loginUrl}}": "",
        "{{portal.loginUrlUrlEncoded}}": "",
    }
    if not portal:
        return replacements

    base_url = _to_string(portal.base_url).strip()
    login_url = _to_string(portal.login_url).strip()
    replacements.update(
        {
            "{{portal.baseUrl}}": base_url,
            "{{portal.loginUrl}}": login_url,
            "{{portal.baseUrlUrlEncoded}}": quote(base_url, safe=""),
            "{{portal.loginUrlUrlEncoded}}": quote(login_url, safe=""),
        }
    )
    return replacements


def build_template_replacement_map(context: TemplateContext) -> Dict[str, str]:
    replacements: Dict[str, str] = {}
    replacements.update(_build_user_replacements(context))
    replacements.update(_build_company_replacements(context))
    replacements.update(_build_portal_replacements(context))
    return replacements


def apply_template_variables(value: str, replacements: Dict[str, str]) -> str:
    if not value:
        return ""
    result = value
    for token, replacement in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if token:
            result = result.replace(token, replacement)
    return result
