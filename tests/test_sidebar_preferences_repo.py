from app.repositories.sidebar_preferences import _coerce_preferences


def test_coerce_preferences_filters_duplicates_and_protected_hidden_key():
    payload = {
        "order": ["/tickets", "/tickets", "__divider__:1", "__spacer__:1"],
        "hidden": ["/admin/profile", "/tickets", "/tickets"],
    }

    result = _coerce_preferences(payload)

    assert result["order"] == ["/tickets", "__divider__:1", "__spacer__:1"]
    assert result["hidden"] == ["/tickets"]
    assert result["groups"] == []


def test_coerce_preferences_validates_groups_and_assigns_each_item_once():
    result = _coerce_preferences(
        {
            "order": ["/shop"],
            "groups": [
                {
                    "id": "__group__:commerce",
                    "label": "  Shop  ",
                    "icon": "shop",
                    "items": ["/shop", "/quotes", "/admin/profile", "__divider__:1"],
                },
                {
                    "id": "__group__:sales",
                    "label": "Sales",
                    "icon": "not-an-icon",
                    "items": ["/quotes", "/orders"],
                },
            ],
        }
    )

    assert result["order"] == ["/shop", "__group__:commerce", "__group__:sales"]
    assert result["groups"] == [
        {"id": "__group__:commerce", "label": "Shop", "icon": "shop", "items": ["/shop", "/quotes"]},
        {"id": "__group__:sales", "label": "Sales", "icon": "folder", "items": ["/orders"]},
    ]


def test_default_layout_groups_menu_logically():
    from app.repositories.sidebar_preferences import (
        ALLOWED_GROUP_ICONS,
        SIDEBAR_LAYOUT_VERSION,
        build_default_sidebar_preferences,
    )

    defaults = build_default_sidebar_preferences()
    groups = {group["id"]: group for group in defaults["groups"]}

    assert defaults["version"] == SIDEBAR_LAYOUT_VERSION
    assert defaults["navigation_style"] == "top"
    assert defaults["order"][:3] == ["/", "/search", "/notifications"]
    assert "/m365" in defaults["order"]
    assert "/tickets" in groups["__group__:default-support"]["items"]
    assert "/admin/users" in groups["__group__:default-admin"]["items"]
    assert all(group["id"] in defaults["order"] for group in defaults["groups"])
    assert all(group["icon"] in ALLOWED_GROUP_ICONS for group in defaults["groups"])
    # The default must survive the same validation as user-saved layouts.
    assert _coerce_preferences(defaults) == defaults
    # Each call returns an independent copy.
    defaults["groups"][0]["items"].clear()
    assert build_default_sidebar_preferences()["groups"][0]["items"]


def test_resolve_stored_preferences_upgrades_legacy_flat_layouts():
    from app.repositories.sidebar_preferences import (
        build_default_sidebar_preferences,
        resolve_stored_preferences,
    )

    assert resolve_stored_preferences(None) == build_default_sidebar_preferences()
    assert resolve_stored_preferences({}) == build_default_sidebar_preferences()

    legacy = resolve_stored_preferences({"order": ["/shop", "/"], "hidden": ["/shop"]})
    assert legacy["groups"] == build_default_sidebar_preferences()["groups"]
    assert legacy["hidden"] == ["/shop"]


def test_resolve_stored_preferences_respects_saved_layouts():
    from app.repositories.sidebar_preferences import resolve_stored_preferences

    # A user who removed every group after grouping existed keeps a flat menu.
    flat = resolve_stored_preferences({"version": 1, "order": ["/shop"], "hidden": [], "groups": []})
    assert flat["groups"] == []
    assert flat["order"] == ["/shop"]

    # Legacy rows that already had custom groups are kept as they are.
    custom = resolve_stored_preferences(
        {"order": [], "groups": [{"id": "__group__:mine", "label": "Mine", "items": ["/shop"]}]}
    )
    assert [group["id"] for group in custom["groups"]] == ["__group__:mine"]

    top = resolve_stored_preferences(
        {"version": 1, "navigation_style": "top", "order": ["/"], "groups": []}
    )
    assert top["navigation_style"] == "top"

    invalid = resolve_stored_preferences(
        {"version": 1, "navigation_style": "floating", "order": ["/"], "groups": []}
    )
    assert invalid["navigation_style"] == "top"

    sidebar = resolve_stored_preferences(
        {"version": 1, "navigation_style": "sidebar", "order": ["/"], "groups": []}
    )
    assert sidebar["navigation_style"] == "sidebar"


def test_reset_deletes_saved_layout_and_returns_default(monkeypatch):
    import asyncio

    from app.repositories import sidebar_preferences

    calls = []

    async def fake_execute(sql, params):
        calls.append((" ".join(sql.split()), params))

    monkeypatch.setattr(sidebar_preferences.db, "execute", fake_execute)

    result = asyncio.run(sidebar_preferences.reset_user_sidebar_preferences(42))

    assert calls == [("DELETE FROM user_sidebar_preferences WHERE user_id = %s", (42,))]
    assert result == sidebar_preferences.build_default_sidebar_preferences()


def test_profile_reset_button_resets_immediately():
    from pathlib import Path

    profile_template = Path("app/templates/admin/profile.html").read_text()
    profile_script = Path("app/static/js/profile.js").read_text()
    base_template = Path("app/templates/base.html").read_text()
    users_routes = Path("app/api/routes/users.py").read_text()

    assert "data-sidebar-reset" in profile_template
    assert "Reset to default" in profile_template
    assert "window.MyPortalSidebarMenu.reset()" in profile_script
    assert "requestSidebarPreferences('DELETE')" in base_template
    assert '@router.delete("/me/sidebar-preferences"' in users_routes


def test_role_switcher_display_defaults_to_full_and_accepts_known_modes():
    from app.repositories.sidebar_preferences import build_default_sidebar_preferences

    assert build_default_sidebar_preferences()["role_switcher_display"] == "full"
    assert _coerce_preferences({})["role_switcher_display"] == "full"
    assert _coerce_preferences(None)["role_switcher_display"] == "full"
    assert _coerce_preferences({"role_switcher_display": "bogus"})["role_switcher_display"] == "full"
    assert _coerce_preferences({"role_switcher_display": "icon"})["role_switcher_display"] == "icon"
    assert _coerce_preferences({"role_switcher_display": "hidden"})["role_switcher_display"] == "hidden"


def test_top_menu_labels_defaults_to_auto_and_accepts_known_modes():
    from app.repositories.sidebar_preferences import build_default_sidebar_preferences

    assert build_default_sidebar_preferences()["top_menu_labels"] == "auto"
    assert _coerce_preferences({})["top_menu_labels"] == "auto"
    assert _coerce_preferences(None)["top_menu_labels"] == "auto"
    assert _coerce_preferences({"top_menu_labels": "bogus"})["top_menu_labels"] == "auto"
    assert _coerce_preferences({"top_menu_labels": "always"})["top_menu_labels"] == "always"


def test_profile_offers_always_show_top_menu_labels():
    from pathlib import Path

    profile_template = Path("app/templates/admin/profile.html").read_text()
    profile_script = Path("app/static/js/profile.js").read_text()
    base_template = Path("app/templates/base.html").read_text()

    assert 'name="top-menu-labels"' in profile_template
    assert "Always show menu labels" in profile_template
    assert "top_menu_labels" in profile_script
    assert "navigation--top-compact" in base_template
    assert "top_menu_labels" in base_template


def test_compact_top_menu_only_hides_top_level_labels():
    from pathlib import Path

    css = Path("app/static/css/app.css").read_text()

    # The compact mode must stay scoped to direct children of the top menu;
    # expanded drop-downs keep their item labels so people know what they
    # are opening.
    assert ".menu > .menu__item > a .menu__label" in css
    assert "navigation--top-compact .menu__item > a .menu__label" not in css


def test_profile_menu_offers_role_switcher_modes_to_super_admins_only():
    from pathlib import Path

    template = Path("app/templates/admin/profile.html").read_text()
    toggle_index = template.index('name="role-switcher-display"')
    guard_index = template.rindex("{% if role_switcher_allowed", 0, toggle_index)
    assert "{% endif %}" not in template[guard_index:toggle_index]
    for mode in ("full", "icon", "hidden"):
        assert f'name="role-switcher-display" value="{mode}"' in template

    base = Path("app/templates/base.html").read_text()
    assert "data-role-switcher-container" in base
    assert "data-role-switcher-toggle" in base
    assert "data-role-switcher-popover" in base
    assert "role_switcher_display" in base
