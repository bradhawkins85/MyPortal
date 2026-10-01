from __future__ import annotations

import pytest

from app.services import m365_mail

pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_delete_post_import_action_uses_graph_delete(monkeypatch):
    deleted: list[tuple[str, str]] = []

    async def fake_delete(token: str, url: str) -> None:
        deleted.append((token, url))

    async def unexpected_patch(*args, **kwargs) -> None:
        raise AssertionError("delete and mark-as-read must be mutually exclusive")

    monkeypatch.setattr(m365_mail, "_graph_delete", fake_delete)
    monkeypatch.setattr(m365_mail, "_graph_patch", unexpected_patch)

    result = await m365_mail._apply_post_import_action(
        access_token="token",
        upn="reports@example.com",
        message_id="message/id",
        mark_as_read=False,
        delete_after_import=True,
        is_unread=True,
    )

    assert result == "deleted"
    assert deleted == [
        (
            "token",
            "https://graph.microsoft.com/v1.0/users/reports%40example.com/messages/message%2Fid",
        )
    ]


async def test_create_rejects_two_post_import_actions():
    with pytest.raises(ValueError, match="either marking messages as read or deleting"):
        await m365_mail.create_account(
            {
                "name": "Reports",
                "user_principal_name": "reports@example.com",
                "mark_as_read": True,
                "delete_after_import": True,
            }
        )
