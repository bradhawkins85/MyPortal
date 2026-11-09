"""Regression: names the RMM check services use at runtime must be bound.

An automated "cyclic import" fix once removed module imports that were still
referenced, so every asset page failed with ``name 'rmm_targeting' is not
defined`` when it listed the device's matching checks.
"""

import builtins
import dis
import importlib
import types

import pytest


def _global_loads(code: types.CodeType):
    for instruction in dis.get_instructions(code):
        if instruction.opname in {"LOAD_GLOBAL", "LOAD_NAME"}:
            yield instruction.argval
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _global_loads(const)


@pytest.mark.parametrize(
    "module_name",
    ["app.services.rmm_checks", "app.services.rmm_action_steps"],
)
def test_every_global_name_is_defined(module_name):
    module = importlib.import_module(module_name)
    with open(module.__file__, encoding="utf-8") as handle:
        code = compile(handle.read(), module.__file__, "exec")
    missing = sorted(
        name
        for name in set(_global_loads(code))
        if not hasattr(module, name) and not hasattr(builtins, name)
    )
    assert missing == []


@pytest.mark.asyncio
async def test_matching_checks_compiles_target_rules():
    from app.services import rmm_checks

    check = {
        "id": 1,
        "scope": "global",
        "target_rules": {"type": "group", "match": "all", "children": []},
    }
    # Must not raise NameError while compiling the rule tree.
    try:
        await rmm_checks.matching_checks_for_agents([check], [])
    except NameError as exc:  # pragma: no cover - the regression
        pytest.fail(str(exc))
