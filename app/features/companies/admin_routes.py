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

from . import handlers

router = APIRouter(tags=["Companies"])


def _add(path: str, endpoint, methods: list[str], **kwargs) -> None:
    router.add_api_route(
        path, endpoint, methods=methods, **kwargs
    )


# --- Core company CRUD & assignment pages ------------------------------------
_add("/admin/companies", handlers.admin_companies_page, ["GET"], response_class=HTMLResponse)
_add(
    "/admin/companies/{company_id}/edit",
    handlers.admin_company_edit_page,
    ["GET"],
    response_class=HTMLResponse,
)
_add("/admin/companies", handlers.admin_create_company, ["POST"], response_class=HTMLResponse)
_add(
    "/admin/companies/assign",
    handlers.admin_assign_user_to_company,
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}",
    handlers.admin_update_company,
    ["POST"],
    response_class=HTMLResponse,
)

# --- Company staff field configuration ---------------------------------------
_add(
    "/admin/companies/{company_id}/staff-fields",
    handlers.admin_update_company_staff_fields,
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/staff-custom-fields",
    handlers.admin_create_company_staff_custom_field,
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/staff-custom-fields/{definition_id}",
    handlers.admin_update_company_staff_custom_field,
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/staff-custom-fields/{definition_id}/delete",
    handlers.admin_delete_company_staff_custom_field,
    ["POST"],
    response_class=HTMLResponse,
)

# --- Company users (create / invite) -----------------------------------------
_add(
    "/admin/companies/users/create",
    handlers.admin_create_company_user,
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/users/invite",
    handlers.admin_invite_company_user,
    ["POST"],
    response_class=HTMLResponse,
)

# --- Company membership assignment management --------------------------------
_add(
    "/admin/companies/assignment/{company_id}/{user_id}/permission",
    handlers.admin_update_company_permission,
    ["POST"],
)
_add(
    "/admin/companies/assignment/{company_id}/{user_id}/staff-permission",
    handlers.admin_update_staff_permission,
    ["POST"],
)
_add(
    "/admin/companies/assignment/{company_id}/{user_id}/role",
    handlers.admin_update_membership_role,
    ["POST"],
)
_add(
    "/admin/companies/assignment/{company_id}/{staff_id}/pending/remove",
    handlers.admin_remove_pending_company_assignment,
    ["POST"],
)
_add(
    "/admin/companies/assignment/{company_id}/{user_id}/remove",
    handlers.admin_remove_company_assignment,
    ["POST"],
)

# --- Billing contacts --------------------------------------------------------
_add(
    "/admin/companies/{company_id}/billing-contacts/add",
    handlers.admin_add_billing_contact,
    ["POST"],
)
_add(
    "/admin/companies/{company_id}/billing-contacts/{staff_id}/remove",
    handlers.admin_remove_billing_contact,
    ["POST"],
)

# --- Microsoft 365 per-company credentials -----------------------------------
_add(
    "/admin/companies/{company_id}/m365-provision",
    handlers.admin_company_m365_provision,
    ["GET"],
)
_add(
    "/admin/companies/{company_id}/m365-discover",
    handlers.admin_company_m365_discover,
    ["GET"],
)
_add(
    "/admin/companies/{company_id}/m365-credentials",
    handlers.admin_save_company_m365_credentials,
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/m365-credentials/delete",
    handlers.admin_delete_company_m365_credentials,
    ["POST"],
    response_class=HTMLResponse,
)

# --- Tray integration (per-company) ------------------------------------------
_add(
    "/admin/companies/{company_id}/tray",
    handlers.admin_company_tray_settings_page,
    ["GET"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/tray",
    handlers.admin_company_tray_settings_save,
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/tray/tokens",
    handlers.admin_company_tray_create_token,
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/companies/{company_id}/tray/tokens/{token_id}/revoke",
    handlers.admin_company_tray_revoke_token,
    ["POST"],
    response_class=HTMLResponse,
)


__all__ = ["router"]
