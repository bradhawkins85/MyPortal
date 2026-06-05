from app.features.matrix_chat_assign import routes


def test_normalize_conditions_clears_conditions_for_default_rule():
    conditions = [{"type": "subject", "operator": "contains", "value": "vip"}]
    assert routes._normalize_conditions(is_default=True, conditions=conditions) == []


def test_normalize_conditions_keeps_conditions_for_non_default_rule():
    conditions = [{"type": "subject", "operator": "contains", "value": "vip"}]
    assert routes._normalize_conditions(is_default=False, conditions=conditions) == conditions
