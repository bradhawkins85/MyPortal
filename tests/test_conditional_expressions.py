"""Test conditional expressions in template variables."""
import pytest

from app.services import conditional_expressions, value_templates


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_find_conditionals_simple():
    """Test finding simple conditional expressions."""
    text = "{{if count:asset:bitdefender > 0 then list:asset:bitdefender}}"
    conditionals = conditional_expressions.find_conditionals(text)
    
    assert len(conditionals) == 1
    full_match, condition, then_value, else_value = conditionals[0]
    assert condition == "count:asset:bitdefender > 0"
    assert then_value == "list:asset:bitdefender"
    assert else_value is None


def test_find_conditionals_with_else():
    """Test finding conditional with else clause."""
    text = '{{if count:asset:bitdefender > 0 then list:asset:bitdefender else "No assets"}}'
    conditionals = conditional_expressions.find_conditionals(text)
    
    assert len(conditionals) == 1
    full_match, condition, then_value, else_value = conditionals[0]
    assert condition == "count:asset:bitdefender > 0"
    assert then_value == "list:asset:bitdefender"
    assert else_value == '"No assets"'


def test_find_multiple_conditionals():
    """Test finding multiple conditionals in same text."""
    text = "{{if x > 0 then a}} and {{if y < 10 then b else c}}"
    conditionals = conditional_expressions.find_conditionals(text)
    
    assert len(conditionals) == 2


def test_parse_value_quoted_string():
    """Test parsing quoted string values."""
    assert conditional_expressions._parse_value('"hello"') == "hello"
    assert conditional_expressions._parse_value("'world'") == "world"


def test_parse_value_numbers():
    """Test parsing numeric values."""
    assert conditional_expressions._parse_value("42") == 42
    assert conditional_expressions._parse_value("3.14") == 3.14
    assert conditional_expressions._parse_value("0") == 0


def test_parse_value_variable():
    """Test parsing variable names."""
    result = conditional_expressions._parse_value("count:asset:bitdefender")
    assert result == "count:asset:bitdefender"


def test_resolve_value_from_token_map():
    """Test resolving values from token map."""
    token_map = {"count:asset:bitdefender": "5"}
    
    result = conditional_expressions._resolve_value("count:asset:bitdefender", token_map)
    assert result == 5  # Should convert to int


def test_resolve_value_literal():
    """Test resolving literal values."""
    token_map = {}
    
    # Numeric literals pass through
    assert conditional_expressions._resolve_value(42, token_map) == 42
    assert conditional_expressions._resolve_value(3.14, token_map) == 3.14
    
    # String literals that aren't in token map pass through
    assert conditional_expressions._resolve_value("hello", token_map) == "hello"


def test_evaluate_comparison_greater_than():
    """Test greater than comparison."""
    assert conditional_expressions._evaluate_comparison(5, ">", 3) is True
    assert conditional_expressions._evaluate_comparison(3, ">", 5) is False
    assert conditional_expressions._evaluate_comparison(5, ">", 5) is False


def test_evaluate_comparison_less_than():
    """Test less than comparison."""
    assert conditional_expressions._evaluate_comparison(3, "<", 5) is True
    assert conditional_expressions._evaluate_comparison(5, "<", 3) is False
    assert conditional_expressions._evaluate_comparison(5, "<", 5) is False


def test_evaluate_comparison_equals():
    """Test equality comparison."""
    assert conditional_expressions._evaluate_comparison(5, "==", 5) is True
    assert conditional_expressions._evaluate_comparison(5, "==", 3) is False
    assert conditional_expressions._evaluate_comparison("hello", "==", "hello") is True


def test_evaluate_comparison_not_equals():
    """Test not equals comparison."""
    assert conditional_expressions._evaluate_comparison(5, "!=", 3) is True
    assert conditional_expressions._evaluate_comparison(5, "!=", 5) is False


def test_evaluate_comparison_string_numbers():
    """Test comparison with string representations of numbers."""
    assert conditional_expressions._evaluate_comparison("10", ">", "5") is True
    assert conditional_expressions._evaluate_comparison("5", "<", "10") is True


def test_evaluate_condition_with_comparison():
    """Test evaluating condition with comparison operator."""
    token_map = {"count:asset:bitdefender": "5"}
    
    assert conditional_expressions._evaluate_condition("count:asset:bitdefender > 0", token_map) is True
    assert conditional_expressions._evaluate_condition("count:asset:bitdefender > 10", token_map) is False
    assert conditional_expressions._evaluate_condition("count:asset:bitdefender == 5", token_map) is True


def test_evaluate_condition_boolean():
    """Test evaluating simple boolean conditions."""
    token_map = {"active": "true", "count": "5", "empty": ""}
    
    # Non-zero numbers are truthy
    assert conditional_expressions._evaluate_condition("count", token_map) is True
    
    # Empty strings are falsy
    assert conditional_expressions._evaluate_condition("empty", token_map) is False


def test_evaluate_conditional_true_without_else():
    """Test conditional evaluation when condition is true and no else clause."""
    token_map = {
        "count:asset:bitdefender": "3",
        "list:asset:bitdefender": "Server-01, Server-02, Server-03"
    }
    
    result = conditional_expressions.evaluate_conditional(
        "count:asset:bitdefender > 0",
        "list:asset:bitdefender",
        None,
        token_map
    )
    
    assert result == "Server-01, Server-02, Server-03"


def test_evaluate_conditional_false_without_else():
    """Test conditional evaluation when condition is false and no else clause."""
    token_map = {"count:asset:bitdefender": "0"}
    
    result = conditional_expressions.evaluate_conditional(
        "count:asset:bitdefender > 0",
        "list:asset:bitdefender",
        None,
        token_map
    )
    
    assert result == ""


def test_evaluate_conditional_true_with_else():
    """Test conditional evaluation when condition is true with else clause."""
    token_map = {
        "count:asset:bitdefender": "3",
        "list:asset:bitdefender": "Server-01, Server-02"
    }
    
    result = conditional_expressions.evaluate_conditional(
        "count:asset:bitdefender > 0",
        "list:asset:bitdefender",
        '"No assets"',
        token_map
    )
    
    assert result == "Server-01, Server-02"


def test_evaluate_conditional_false_with_else():
    """Test conditional evaluation when condition is false with else clause."""
    token_map = {"count:asset:bitdefender": "0"}
    
    result = conditional_expressions.evaluate_conditional(
        "count:asset:bitdefender > 0",
        "list:asset:bitdefender",
        '"No Bitdefender assets found"',
        token_map
    )
    
    assert result == "No Bitdefender assets found"


def test_process_conditionals_simple():
    """Test processing simple conditional in text."""
    text = "Assets: {{if count:asset:bitdefender > 0 then list:asset:bitdefender}}"
    token_map = {
        "count:asset:bitdefender": "2",
        "list:asset:bitdefender": "Server-01, Server-02"
    }
    
    result = conditional_expressions.process_conditionals(text, token_map)
    assert result == "Assets: Server-01, Server-02"


def test_process_conditionals_with_else():
    """Test processing conditional with else clause."""
    text = '{{if count:asset:bitdefender > 0 then list:asset:bitdefender else "None"}}'
    token_map = {"count:asset:bitdefender": "0"}
    
    result = conditional_expressions.process_conditionals(text, token_map)
    assert result == "None"


def test_process_conditionals_multiple():
    """Test processing multiple conditionals in same text."""
    text = "BD: {{if x > 0 then a else b}}, TL: {{if y > 0 then c else d}}"
    token_map = {"x": "5", "y": "0", "a": "yes", "b": "no", "c": "yes", "d": "no"}
    
    result = conditional_expressions.process_conditionals(text, token_map)
    assert result == "BD: yes, TL: no"


def test_process_conditionals_empty_text():
    """Test processing with empty or None text."""
    assert conditional_expressions.process_conditionals("", {}) == ""
    assert conditional_expressions.process_conditionals(None, {}) is None


def test_comparison_operators_all():
    """Test all comparison operators."""
    token_map = {"value": "10"}
    
    # Greater than
    assert conditional_expressions._evaluate_condition("value > 5", token_map) is True
    assert conditional_expressions._evaluate_condition("value > 15", token_map) is False
    
    # Less than
    assert conditional_expressions._evaluate_condition("value < 15", token_map) is True
    assert conditional_expressions._evaluate_condition("value < 5", token_map) is False
    
    # Greater than or equal
    assert conditional_expressions._evaluate_condition("value >= 10", token_map) is True
    assert conditional_expressions._evaluate_condition("value >= 5", token_map) is True
    assert conditional_expressions._evaluate_condition("value >= 15", token_map) is False
    
    # Less than or equal
    assert conditional_expressions._evaluate_condition("value <= 10", token_map) is True
    assert conditional_expressions._evaluate_condition("value <= 15", token_map) is True
    assert conditional_expressions._evaluate_condition("value <= 5", token_map) is False
    
    # Equals
    assert conditional_expressions._evaluate_condition("value == 10", token_map) is True
    assert conditional_expressions._evaluate_condition("value == 5", token_map) is False
    
    # Not equals
    assert conditional_expressions._evaluate_condition("value != 5", token_map) is True
    assert conditional_expressions._evaluate_condition("value != 10", token_map) is False


@pytest.mark.anyio
async def test_conditional_with_dynamic_variables(monkeypatch):
    """Test conditional expressions with dynamic variables in value_templates."""
    # Mock the repository functions
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        return 3 if field_name == "bitdefender" else 0
    
    async def fake_list_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        if field_name == "bitdefender":
            return ["Server-01", "Server-02", "Workstation-03"]
        return []
    
    from app.services import dynamic_variables
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "list_assets_by_custom_field",
        fake_list_assets_by_custom_field,
    )
    
    template = "{{if count:asset:bitdefender > 0 then list:asset:bitdefender else 'No assets'}}"
    context = {"company_id": 42}
    
    result = await value_templates.render_string_async(template, context)
    assert result == "Server-01, Server-02, Workstation-03"


@pytest.mark.anyio
async def test_conditional_with_zero_count(monkeypatch):
    """Test conditional when count is zero (else clause)."""
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        return 0
    
    async def fake_list_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        return []
    
    from app.services import dynamic_variables
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "list_assets_by_custom_field",
        fake_list_assets_by_custom_field,
    )
    
    template = '{{if count:asset:bitdefender > 0 then list:asset:bitdefender else "No Bitdefender assets"}}'
    context = {"company_id": 42}
    
    result = await value_templates.render_string_async(template, context)
    assert result == "No Bitdefender assets"


@pytest.mark.anyio
async def test_conditional_in_complex_template(monkeypatch):
    """Test conditional in a more complex template with regular tokens."""
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        counts = {"bitdefender": 5, "webroot": 0}
        return counts.get(field_name, 0)
    
    async def fake_list_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        lists = {
            "bitdefender": ["Server-01", "Server-02"],
        }
        return lists.get(field_name, [])
    
    from app.services import dynamic_variables
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "list_assets_by_custom_field",
        fake_list_assets_by_custom_field,
    )
    
    template = """Security Report:
Bitdefender: {{if count:asset:bitdefender > 0 then list:asset:bitdefender else "Not installed"}}
Webroot: {{if count:asset:webroot > 0 then list:asset:webroot else "Not installed"}}"""
    
    context = {"company_id": 42}
    
    result = await value_templates.render_string_async(template, context)
    assert "Server-01, Server-02" in result
    assert "Not installed" in result


@pytest.mark.anyio
async def test_conditional_with_numeric_literal(monkeypatch):
    """Test conditional comparing against numeric literals."""
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        return 15
    
    from app.services import dynamic_variables
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    
    # Test >= 10
    template = '{{if count:asset:bitdefender >= 10 then "Many assets" else "Few assets"}}'
    context = {"company_id": 42}
    
    result = await value_templates.render_string_async(template, context)
    assert result == "Many assets"


@pytest.mark.anyio
async def test_conditional_nested_in_payload(monkeypatch):
    """Test conditional in a nested payload structure."""
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        return 3
    
    async def fake_list_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        return ["Asset-1", "Asset-2", "Asset-3"]
    
    from app.services import dynamic_variables
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "list_assets_by_custom_field",
        fake_list_assets_by_custom_field,
    )
    
    payload = {
        "title": "Asset Report",
        "summary": "{{if count:asset:bitdefender > 0 then list:asset:bitdefender else 'None'}}",
        "count": "{{count:asset:bitdefender}}",
    }
    context = {"company_id": 42}
    
    result = await value_templates.render_value_async(payload, context)
    
    assert result["title"] == "Asset Report"
    assert result["summary"] == "Asset-1, Asset-2, Asset-3"
    assert result["count"] == "3"


def test_case_insensitive_if_keyword():
    """Test that 'if' keyword is case-insensitive."""
    text1 = "{{if x > 0 then a}}"
    text2 = "{{IF x > 0 then a}}"
    text3 = "{{If x > 0 THEN a}}"
    
    token_map = {"x": "5", "a": "yes"}
    
    assert conditional_expressions.process_conditionals(text1, token_map) == "yes"
    assert conditional_expressions.process_conditionals(text2, token_map) == "yes"
    assert conditional_expressions.process_conditionals(text3, token_map) == "yes"
