"""Role matrix for mailbox capabilities independent of license administration."""

import pytest

from app.main import _m365_mailbox_capabilities, _require_mailbox_capability


@pytest.mark.parametrize(
    ("user", "membership", "expected"),
    [
        ({"is_super_admin": True}, {}, (True, True, True, True)),
        ({}, {"menu_permissions": {"menu.m365.configuration": "write"}}, (False, False, False, False)),
        ({}, {"menu_permissions": {"menu.m365.user_mailboxes": "read"}}, (True, False, False, False)),
        ({}, {"menu_permissions": {"menu.m365.user_mailboxes": "write"}}, (True, True, False, False)),
        ({}, {"can_view_m365_shared_mailboxes": True}, (False, False, True, False)),
        ({}, {"can_manage_licenses": True}, (False, False, False, False)),
    ],
)
def test_mailbox_role_matrix(user, membership, expected):
    capabilities = _m365_mailbox_capabilities(user, membership)
    assert (
        capabilities["user_read"],
        capabilities["user_write"],
        capabilities["shared_read"],
        capabilities["shared_write"],
    ) == expected
    assert capabilities["sync"] is bool(user.get("is_super_admin"))


def test_read_only_capability_cannot_be_used_for_a_write():
    capabilities = _m365_mailbox_capabilities(
        {}, {"menu_permissions": {"menu.m365.user_mailboxes": "read"}}
    )
    with pytest.raises(Exception) as exc:
        _require_mailbox_capability(capabilities, "user_write")
    assert getattr(exc.value, "status_code", None) == 403
