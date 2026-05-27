"""Company admin handlers for the ``companies`` feature pack."""

from __future__ import annotations

from fastapi import Request


def _main():
    from app import main as main_module

    return main_module


async def admin_companies_page(request: Request):
    return await _main().admin_companies_page(request=request)


async def admin_company_edit_page(company_id: int, request: Request):
    return await _main().admin_company_edit_page(company_id=company_id, request=request)


async def admin_create_company(request: Request):
    return await _main().admin_create_company(request=request)


async def admin_assign_user_to_company(request: Request):
    return await _main().admin_assign_user_to_company(request=request)


async def admin_update_company(company_id: int, request: Request):
    return await _main().admin_update_company(company_id=company_id, request=request)


async def admin_update_company_staff_fields(company_id: int, request: Request):
    return await _main().admin_update_company_staff_fields(company_id=company_id, request=request)


async def admin_create_company_staff_custom_field(company_id: int, request: Request):
    return await _main().admin_create_company_staff_custom_field(company_id=company_id, request=request)


async def admin_update_company_staff_custom_field(company_id: int, definition_id: int, request: Request):
    return await _main().admin_update_company_staff_custom_field(
        company_id=company_id,
        definition_id=definition_id,
        request=request,
    )


async def admin_delete_company_staff_custom_field(company_id: int, definition_id: int, request: Request):
    return await _main().admin_delete_company_staff_custom_field(
        company_id=company_id,
        definition_id=definition_id,
        request=request,
    )


async def admin_create_company_user(request: Request):
    return await _main().admin_create_company_user(request=request)


async def admin_invite_company_user(request: Request):
    return await _main().admin_invite_company_user(request=request)


async def admin_update_company_permission(company_id: int, user_id: int, request: Request):
    return await _main().admin_update_company_permission(
        company_id=company_id,
        user_id=user_id,
        request=request,
    )


async def admin_update_staff_permission(company_id: int, user_id: int, request: Request):
    return await _main().admin_update_staff_permission(
        company_id=company_id,
        user_id=user_id,
        request=request,
    )


async def admin_update_membership_role(company_id: int, user_id: int, request: Request):
    return await _main().admin_update_membership_role(
        company_id=company_id,
        user_id=user_id,
        request=request,
    )


async def admin_remove_pending_company_assignment(company_id: int, staff_id: int, request: Request):
    return await _main().admin_remove_pending_company_assignment(
        company_id=company_id,
        staff_id=staff_id,
        request=request,
    )


async def admin_remove_company_assignment(company_id: int, user_id: int, request: Request):
    return await _main().admin_remove_company_assignment(
        company_id=company_id,
        user_id=user_id,
        request=request,
    )


async def admin_add_billing_contact(company_id: int, request: Request):
    return await _main().admin_add_billing_contact(company_id=company_id, request=request)


async def admin_remove_billing_contact(company_id: int, staff_id: int, request: Request):
    return await _main().admin_remove_billing_contact(
        company_id=company_id,
        staff_id=staff_id,
        request=request,
    )


async def admin_company_m365_provision(company_id: int, request: Request):
    return await _main().admin_company_m365_provision(company_id=company_id, request=request)


async def admin_company_m365_discover(company_id: int, request: Request):
    return await _main().admin_company_m365_discover(company_id=company_id, request=request)


async def admin_save_company_m365_credentials(company_id: int, request: Request):
    return await _main().admin_save_company_m365_credentials(company_id=company_id, request=request)


async def admin_delete_company_m365_credentials(company_id: int, request: Request):
    return await _main().admin_delete_company_m365_credentials(company_id=company_id, request=request)


async def admin_company_tray_settings_page(company_id: int, request: Request):
    return await _main().admin_company_tray_settings_page(company_id=company_id, request=request)


async def admin_company_tray_settings_save(company_id: int, request: Request):
    return await _main().admin_company_tray_settings_save(company_id=company_id, request=request)


async def admin_company_tray_create_token(company_id: int, request: Request):
    return await _main().admin_company_tray_create_token(company_id=company_id, request=request)


async def admin_company_tray_revoke_token(company_id: int, token_id: int, request: Request):
    return await _main().admin_company_tray_revoke_token(
        company_id=company_id,
        token_id=token_id,
        request=request,
    )
