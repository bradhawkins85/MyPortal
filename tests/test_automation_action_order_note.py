from app.services import automations as automations_service


def test_normalise_actions_sorts_by_order():
    actions = [
        {"module": "third", "payload": {}, "order": 2},
        {"module": "first", "payload": {}, "order": 0},
        {"module": "second", "payload": {}, "order": 1},
    ]
    result = automations_service._normalise_actions(actions)
    assert [a["module"] for a in result] == ["first", "second", "third"]


def test_normalise_actions_preserves_note():
    actions = [
        {"module": "email", "payload": {}, "order": 0, "note": "Send confirmation email"},
    ]
    result = automations_service._normalise_actions(actions)
    assert result[0]["note"] == "Send confirmation email"


def test_normalise_actions_skips_empty_note():
    actions = [
        {"module": "email", "payload": {}, "order": 0, "note": ""},
    ]
    result = automations_service._normalise_actions(actions)
    assert "note" not in result[0]


def test_normalise_actions_defaults_order_to_index():
    actions = [
        {"module": "first", "payload": {}},
        {"module": "second", "payload": {}},
    ]
    result = automations_service._normalise_actions(actions)
    assert result[0]["module"] == "first"
    assert result[1]["module"] == "second"
    assert result[0]["order"] == 0
    assert result[1]["order"] == 1


def test_normalise_actions_handles_mixed_order_and_missing():
    actions = [
        {"module": "explicit", "payload": {}, "order": 10},
        {"module": "implicit", "payload": {}},
    ]
    result = automations_service._normalise_actions(actions)
    # "implicit" is at index 1, so it gets order=1, which is less than 10 → comes first
    assert result[0]["module"] == "implicit"
    assert result[1]["module"] == "explicit"
