"""Company admin handlers for the ``companies`` feature pack."""

from __future__ import annotations


def _main():
    from app import main as main_module

    return main_module


admin_companies_page = _main().admin_companies_page
admin_company_edit_page = _main().admin_company_edit_page
admin_create_company = _main().admin_create_company
admin_assign_user_to_company = _main().admin_assign_user_to_company
admin_update_company = _main().admin_update_company
admin_update_company_staff_fields = _main().admin_update_company_staff_fields
admin_create_company_staff_custom_field = _main().admin_create_company_staff_custom_field
admin_update_company_staff_custom_field = _main().admin_update_company_staff_custom_field
admin_delete_company_staff_custom_field = _main().admin_delete_company_staff_custom_field
admin_create_company_user = _main().admin_create_company_user
admin_invite_company_user = _main().admin_invite_company_user
admin_update_company_permission = _main().admin_update_company_permission
admin_update_staff_permission = _main().admin_update_staff_permission
admin_update_membership_role = _main().admin_update_membership_role
admin_remove_pending_company_assignment = _main().admin_remove_pending_company_assignment
admin_remove_company_assignment = _main().admin_remove_company_assignment
admin_add_billing_contact = _main().admin_add_billing_contact
admin_remove_billing_contact = _main().admin_remove_billing_contact
admin_company_m365_provision = _main().admin_company_m365_provision
admin_company_m365_discover = _main().admin_company_m365_discover
admin_save_company_m365_credentials = _main().admin_save_company_m365_credentials
admin_delete_company_m365_credentials = _main().admin_delete_company_m365_credentials
admin_company_tray_settings_page = _main().admin_company_tray_settings_page
admin_company_tray_settings_save = _main().admin_company_tray_settings_save
admin_company_tray_create_token = _main().admin_company_tray_create_token
admin_company_tray_revoke_token = _main().admin_company_tray_revoke_token

