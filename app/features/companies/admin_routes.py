"""Admin company routes for the ``companies`` feature pack.

Mirrors the routes that live in :mod:`app.main` under
``/admin/companies/…``.  URLs and behaviour are intentionally
identical to the in-line handlers so external links, bookmarks, and
tests keep working — the pack re-registers the same handler functions
on its own router so the feature registry tracks them under the
``companies`` slug.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Companies"])


def _main():
    """Return the ``app.main`` module (lazy import)."""
    from app import main as main_module

    return main_module


def _add(path: str, endpoint_name: str, methods: list[str], **kwargs) -> None:
    router.add_api_route(
        path, getattr(_main(), endpoint_name), methods=methods, **kwargs
    )


# --- Core company CRUD & assignment pages ------------------------------------
_add("/admin/companies", "admin_companies_page", ["GET"], response_class=HTMLResponse)
_add(
    "/admin/companies/{company_id}/edit",
    "admin_company_edit_page",
    ["GET"],
    response_class=HTMLResponse,
)
_add("/admin/companies", "admin_create_company", ["POST"], response_class=HTMLResponse)
_add(
    "/admin/companies/assign",
    "admin_assign_user_to_company",
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}",
    "admin_update_company",
    ["POST"],
    response_class=HTMLResponse,
)

# --- Company staff field configuration ---------------------------------------
_add(
    "/admin/companies/{company_id}/staff-fields",
    "admin_update_company_staff_fields",
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/staff-custom-fields",
    "admin_create_company_staff_custom_field",
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/staff-custom-fields/{definition_id}",
    "admin_update_company_staff_custom_field",
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/staff-custom-fields/{definition_id}/delete",
    "admin_delete_company_staff_custom_field",
    ["POST"],
    response_class=HTMLResponse,
)

# --- Company users (create / invite) -----------------------------------------
_add(
    "/admin/companies/users/create",
    "admin_create_company_user",
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/users/invite",
    "admin_invite_company_user",
    ["POST"],
    response_class=HTMLResponse,
)

# --- Company membership assignment management --------------------------------
_add(
    "/admin/companies/assignment/{company_id}/{user_id}/permission",
    "admin_update_company_permission",
    ["POST"],
)
_add(
    "/admin/companies/assignment/{company_id}/{user_id}/staff-permission",
    "admin_update_staff_permission",
    ["POST"],
)
_add(
    "/admin/companies/assignment/{company_id}/{user_id}/role",
    "admin_update_membership_role",
    ["POST"],
)
_add(
    "/admin/companies/assignment/{company_id}/{staff_id}/pending/remove",
    "admin_remove_pending_company_assignment",
    ["POST"],
)
_add(
    "/admin/companies/assignment/{company_id}/{user_id}/remove",
    "admin_remove_company_assignment",
    ["POST"],
)

# --- Billing contacts --------------------------------------------------------
_add(
    "/admin/companies/{company_id}/billing-contacts/add",
    "admin_add_billing_contact",
    ["POST"],
)
_add(
    "/admin/companies/{company_id}/billing-contacts/{staff_id}/remove",
    "admin_remove_billing_contact",
    ["POST"],
)

# --- Microsoft 365 per-company credentials -----------------------------------
_add(
    "/admin/companies/{company_id}/m365-provision",
    "admin_company_m365_provision",
    ["GET"],
)
_add(
    "/admin/companies/{company_id}/m365-discover",
    "admin_company_m365_discover",
    ["GET"],
)
_add(
    "/admin/companies/{company_id}/m365-credentials",
    "admin_save_company_m365_credentials",
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/m365-credentials/delete",
    "admin_delete_company_m365_credentials",
    ["POST"],
    response_class=HTMLResponse,
)

# --- Tray integration (per-company) ------------------------------------------
_add(
    "/admin/companies/{company_id}/tray",
    "admin_company_tray_settings_page",
    ["GET"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/tray",
    "admin_company_tray_settings_save",
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/tray/tokens",
    "admin_company_tray_create_token",
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/tray/tokens/{token_id}/revoke",
    "admin_company_tray_revoke_token",
    ["POST"],
    response_class=HTMLResponse,
)


__all__ = ["router"]
