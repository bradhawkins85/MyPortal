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
