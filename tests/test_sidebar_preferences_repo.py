from app.repositories.sidebar_preferences import _coerce_preferences


def test_coerce_preferences_filters_duplicates_and_protected_hidden_key():
    payload = {
        "order": ["/tickets", "/tickets", "__divider__:1", "__spacer__:1"],
        "hidden": ["/admin/profile", "/tickets", "/tickets"],
    }

    result = _coerce_preferences(payload)

    assert result["order"] == ["/tickets", "__divider__:1", "__spacer__:1"]
    assert result["hidden"] == ["/tickets"]
