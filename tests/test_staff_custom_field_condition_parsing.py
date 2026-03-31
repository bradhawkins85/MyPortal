from app.main import _parse_staff_custom_field_condition


def test_select_map_json_condition_is_normalized():
    parent, operator, value = _parse_staff_custom_field_condition(
        parent_name_value="Department",
        operator_value="select_map",
        condition_value='{"Sales": "A,B,C", "Fallback": "X,Y,Z"}',
    )

    assert parent == "department"
    assert operator == "select_map"
    assert value == '{"Sales":"A,B,C","Fallback":"X,Y,Z"}'


def test_select_map_legacy_condition_is_preserved():
    parent, operator, value = _parse_staff_custom_field_condition(
        parent_name_value="Department",
        operator_value="select_map",
        condition_value="sales=>a|b|c;*=>other",
    )

    assert parent == "department"
    assert operator == "select_map"
    assert value == "sales=>a|b|c;*=>other"


def test_one_of_condition_is_supported():
    parent, operator, value = _parse_staff_custom_field_condition(
        parent_name_value="Department",
        operator_value="one_of",
        condition_value="sales, support, hr",
    )

    assert parent == "department"
    assert operator == "one_of"
    assert value == "sales, support, hr"
