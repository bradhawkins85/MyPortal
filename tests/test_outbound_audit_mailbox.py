from types import SimpleNamespace

import pytest

from app.services import m365_mail


@pytest.mark.anyio
async def test_audit_mailbox_is_excluded_from_routine_sync(monkeypatch):
    account = {
        "id": 7,
        "active": True,
        "user_principal_name": "Audit@Example.com",
    }

    async def get_account(_account_id):
        return account

    async def record_history(**_kwargs):
        return None

    monkeypatch.setattr(m365_mail.mail_repo, "get_account", get_account)
    monkeypatch.setattr(m365_mail, "_record_sync_history_safe", record_history)
    monkeypatch.setattr(
        m365_mail,
        "get_settings",
        lambda: SimpleNamespace(outbound_audit_bcc="audit@example.com"),
    )
    monkeypatch.setattr(
        m365_mail.system_state, "is_restart_pending", lambda: False
    )

    result = await m365_mail.sync_account(7)

    assert result == {
        "status": "skipped",
        "reason": "Audit mailbox requires manual recovery import",
    }


def test_recovery_import_api_is_documented():
    from app.features.m365_mail.api_routes import router

    route = next(
        route
        for route in router.routes
        if route.path == "/m365-mail/accounts/{account_id}/recovery-import"
    )
    assert route.summary == (
        "Manually import a mailbox or folder without notifications"
    )
