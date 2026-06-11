from app.security.menu_permissions import (
    ACCESS_LEVEL_LABELS,
    DEFAULT_ACCESS_LEVELS,
    MENU_PERMISSION_BY_KEY,
    MENU_PERMISSIONS,
    MenuAccessLevel,
    permission_for_route,
)


def test_menu_permission_keys_are_unique_and_have_required_metadata():
    keys = [permission.key for permission in MENU_PERMISSIONS]

    assert len(keys) == len(set(keys))
    for permission in MENU_PERMISSIONS:
        assert permission.key.startswith("menu.")
        assert permission.label
        assert permission.route_prefixes
        assert permission.supported_access_levels == DEFAULT_ACCESS_LEVELS
        assert {level.value for level in permission.supported_access_levels} == {"none", "read", "write"}


def test_access_level_labels_are_stable():
    assert ACCESS_LEVEL_LABELS[MenuAccessLevel.NONE.value] == "No Access"
    assert ACCESS_LEVEL_LABELS[MenuAccessLevel.READ.value] == "Read Only"
    assert ACCESS_LEVEL_LABELS[MenuAccessLevel.WRITE.value] == "Read/Write"


def test_office_365_menu_is_split_into_child_permissions():
    expected = {
        "menu.m365.configuration",
        "menu.m365.best_practices",
        "menu.m365.user_mailboxes",
        "menu.m365.shared_mailboxes",
        "menu.m365.licenses",
        "menu.m365.diagnostics",
    }

    assert expected.issubset(MENU_PERMISSION_BY_KEY)
    assert {MENU_PERMISSION_BY_KEY[key].group for key in expected} == {"Office 365"}
    assert MENU_PERMISSION_BY_KEY["menu.m365.configuration"].route_prefixes == ("/m365",)
    assert MENU_PERMISSION_BY_KEY["menu.m365.user_mailboxes"].route_prefixes == ("/m365/mailboxes/users",)
    assert MENU_PERMISSION_BY_KEY["menu.m365.shared_mailboxes"].route_prefixes == ("/m365/mailboxes/shared",)


def test_permission_for_route_prefers_most_specific_prefix():
    assert permission_for_route("/m365/mailboxes/users").key == "menu.m365.user_mailboxes"
    assert permission_for_route("/m365/mailboxes/shared/details").key == "menu.m365.shared_mailboxes"
    assert permission_for_route("/m365/best-practices/settings").key == "menu.m365.best_practices"
    assert permission_for_route("/licenses").key == "menu.m365.licenses"
    assert permission_for_route("/compliance-checks").key == "menu.compliance_checks.my_checks"
    assert permission_for_route("/admin/compliance-checks/library").key == "menu.compliance_checks.library"


def test_permission_for_route_does_not_treat_dashboard_as_catch_all():
    assert permission_for_route("/").key == "menu.dashboard"
    assert permission_for_route("/not-a-real-menu") is None
