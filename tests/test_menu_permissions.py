from app.security.menu_permissions import (
    compact_menu_permissions,
    menu_has_access,
    menu_permissions_to_legacy,
    normalize_menu_permissions,
)


def test_legacy_permissions_normalize_to_tri_state_menu_permissions():
    permissions = normalize_menu_permissions([
        "m365_user_mailboxes.access",
        "licenses.manage",
        "cart.access",
    ])

    assert permissions["menu.m365.user_mailboxes"] == "read"
    assert permissions["menu.m365.licenses"] == "write"
    assert permissions["menu.subscriptions"] == "write"


def test_compact_menu_permissions_removes_no_access_entries():
    compact = compact_menu_permissions(
        {
            "menu.m365.configuration": "No Access",
            "menu.m365.user_mailboxes": "Read Only",
            "menu.subscriptions": "Read/Write",
        }
    )

    assert compact == {
        "menu.m365.user_mailboxes": "read",
        "menu.subscriptions": "write",
    }


def test_menu_has_access_enforces_read_vs_write():
    menu_access = {"menu.m365.user_mailboxes": "read"}

    assert menu_has_access(menu_access, "menu.m365.user_mailboxes") is True
    assert menu_has_access(menu_access, "menu.m365.user_mailboxes", write=True) is False


def test_menu_permissions_to_legacy_keeps_backward_compatibility():
    legacy = menu_permissions_to_legacy({"menu.m365.user_mailboxes": "read", "menu.assets": "write"})

    assert "m365_user_mailboxes.access" in legacy
    assert "assets.manage" in legacy


def test_bcp_continuity_menu_permission_expands_to_legacy_permissions():
    legacy_read = menu_permissions_to_legacy({"menu.continuity": "read"})
    legacy_write = menu_permissions_to_legacy({"menu.continuity": "write"})

    assert "continuity.access" in legacy_read
    assert "bcp:view" in legacy_read
    assert "bcp:edit" not in legacy_read
    assert "bcp:edit" in legacy_write
