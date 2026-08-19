from app.main import _scheduled_task_command_label


def test_scheduled_task_commands_have_friendly_labels():
    assert _scheduled_task_command_label("update_products") == "Update Shop Products"
    assert _scheduled_task_command_label("update_stock_feed") == "Update Shop Stock Feed"
    assert _scheduled_task_command_label("system_update") == "Update MyPortal system"
    assert _scheduled_task_command_label("sync_tactical_assets") == "Sync Tactical RMM assets"
    assert _scheduled_task_command_label("m365_mail_sync:11") == "Sync Microsoft 365 mailbox 11"


def test_unknown_scheduled_task_commands_are_humanised():
    assert _scheduled_task_command_label("custom_report_refresh") == "Custom Report Refresh"
