import pytest

from app.schemas.staff_onboarding_workflows import WorkflowStepDefinition
from app.services.staff_onboarding_workflows import _resolve_template_value
from app.services.workflow_variable_functions import (
    VariableFunctionError,
    apply_pipeline,
    parse_pipeline,
)

@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


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


@pytest.mark.anyio
async def test_create_user_sends_function_shaped_upn_to_graph(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services import staff_onboarding_workflows as workflows

    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    graph_post = AsyncMock(return_value={"id": "user-1"})
    monkeypatch.setattr(workflows.m365_service, "_graph_post", graph_post)
    vars_map = {"staff": {"first_name": " Jane ", "last_name": "O'Citizen"}}
    raw_step = {
        "type": "create_user",
        "user_principal_name": "${vars.staff.first_name | trim | alnum | lower}"
        "${vars.staff.last_name | trim | alnum | left(1) | lower}@alchemycs.com.au",
        "display_name": "${vars.staff.first_name | trim} ${vars.staff.last_name}",
    }
    step = workflows._resolve_template_value(raw_step, vars_map=vars_map)

    result = await workflows._execute_policy_step(
        step=step,
        company_id=9,
        staff={"id": 1, "email": ""},
        policy_config={},
        vars_map=vars_map,
    )

    payload = graph_post.await_args_list[0].args[2]
    assert payload["userPrincipalName"] == "janeo@alchemycs.com.au"
    assert payload["mailNickname"] == "janeo"
    assert payload["displayName"] == "Jane O'Citizen"
    assert result["user_principal_name"] == "janeo@alchemycs.com.au"
