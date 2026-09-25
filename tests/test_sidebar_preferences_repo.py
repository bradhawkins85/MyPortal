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
