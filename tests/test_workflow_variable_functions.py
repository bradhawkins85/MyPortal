import pytest

from app.schemas.staff_onboarding_workflows import WorkflowStepDefinition
from app.services.staff_onboarding_workflows import _resolve_template_value
from app.services.workflow_variable_functions import (
    VariableFunctionError,
    apply_pipeline,
    parse_pipeline,
)

VARS = {"staff": {"first_name": "John", "last_name": " Smith ", "full_name": "John Smith"}}


def render(text):
    return _resolve_template_value(text, vars_map=VARS)


def test_email_format_from_first_name_and_last_initial():
    assert render("${vars.staff.first_name}${vars.staff.last_name | trim | left(1)}@mycompany.com.au") == "JohnS@mycompany.com.au"


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("${vars.staff.full_name | right(3)}", "ith"),
        ("${vars.staff.full_name | mid(6, 2)}", "Sm"),
        ("${vars.staff.full_name | mid(6)}", "Smith"),
        ("${vars.staff.full_name | lower}", "john smith"),
        ("${vars.staff.full_name | replace(' ', '.')}", "John.Smith"),
        ("${vars.staff.full_name | replace(' ')}", "JohnSmith"),
        ("${vars.staff.full_name | word(2)}", "Smith"),
        ("${vars.staff.full_name | last_word}", "Smith"),
        ("${vars.staff.full_name | initials}", "JS"),
        ("${vars.missing | default(n/a)}", "n/a"),
        ("x${vars.staff.full_name | len}", "x10"),
    ],
)
def test_functions(expr, expected):
    assert render(expr) == expected


def test_exact_match_len_returns_int():
    assert render("${vars.staff.full_name | len}") == 10


def test_plain_variables_still_work():
    assert render("${vars.staff.first_name}") == "John"
    assert render("Hi ${vars.staff.first_name}!") == "Hi John!"


def test_bad_function_leaves_token_untouched():
    assert render("${vars.staff.first_name | nope}") == "${vars.staff.first_name | nope}"


def test_parse_errors():
    with pytest.raises(VariableFunctionError):
        parse_pipeline("x | unknown")
    with pytest.raises(VariableFunctionError):
        parse_pipeline("x | replace('a)")
    _, calls = parse_pipeline("x | left(a)")
    with pytest.raises(VariableFunctionError):
        apply_pipeline("abc", calls)


def test_schema_rejects_unknown_function():
    with pytest.raises(ValueError, match="unknown function"):
        WorkflowStepDefinition(key="s", name="S", config={"email": "${vars.staff.first_name | bogus}"})
    WorkflowStepDefinition(key="s", name="S", config={"email": "${vars.staff.first_name | left(1)}"})
