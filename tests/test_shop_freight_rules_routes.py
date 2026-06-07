from app.features.shop import handlers


def test_normalise_freight_conditions_clears_conditions_for_default_rule():
    conditions = [{"type": "dispatch_warehouse", "operator": "equals", "value": "NSW"}]
    assert (
        handlers._normalise_freight_conditions(is_default=True, conditions=conditions)
        == []
    )


def test_normalise_freight_conditions_keeps_conditions_for_non_default_rule():
    conditions = [{"type": "dispatch_warehouse", "operator": "equals", "value": "NSW"}]
    assert (
        handlers._normalise_freight_conditions(is_default=False, conditions=conditions)
        == conditions
    )
