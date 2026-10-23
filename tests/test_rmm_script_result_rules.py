"""Script result rules: matching, saving, tickets and follow-up scripts."""
from __future__ import annotations

import pytest

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_script_result_rules as rules_repo
from app.services import rmm_script_result_rules as rules
from app.services import rmm_scripts
from tests.test_rmm_scripts import _enrol, _environment, _sync, anyio_backend, sqlite_db  # noqa: F401 - fixtures

CHECK_SCRIPT = "Write-Output 'checking'\n"
FIX_SCRIPT = "param([string]$Reason, [string]$Drive)\nWrite-Output $Reason\n"
COMMON = {"user_id": 9, "is_super_admin": False, "company_id": 1}


def _result(**overrides):
    result = {"run_id": 1, "script": "Check", "status": "completed", "exit_code": 0,
              "output": "Free space on C: 4.5 GB\nERROR: disk nearly full", "errors": "", "error_message": "",
              "values": {"BitLocker": "Off"}}
    result.update(overrides)
    return result


async def _scripts(monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/Check.ps1": CHECK_SCRIPT, "rmm/Common/Fix.ps1": FIX_SCRIPT,
                              "rmm/Companies/Other/Theirs.ps1": "Write-Output x\n"})
    return {script["name"]: script for script in await rmm_repo.list_scripts()}


async def _finish(agent, run_id, payload):
    assert await rmm_scripts.mark_started(agent, run_id)
    return await rmm_scripts.record_result(agent, run_id, payload)


async def _queue(script, agent):
    return await rmm_scripts.queue_for_agent(
        script=await rmm_repo.get_script(script["id"], with_content=True),
        agent={**agent, "company_id": 1, "asset_id": 10}, entries={}, inputs=[], timeout_seconds=60,
        requested_by_user_id=9,
    )


def test_conditions_cover_text_numbers_exit_codes_regex_and_values():
    result = _result()
    cases = [
        ({"field": "output", "operator": "contains", "value": "error:"}, True),
        ({"field": "output", "operator": "not_contains", "value": "ERROR"}, False),
        ({"field": "output", "operator": "starts_with", "value": "free space"}, True),
        ({"field": "output", "operator": "ends_with", "value": "FULL"}, True),
        ({"field": "output", "operator": "lt", "value": "5"}, True),  # the first number, 4.5
        ({"field": "output", "operator": "ge", "value": "5"}, False),
        ({"field": "output", "operator": "matches", "value": r"^error: (\w+)"}, True),  # per line, any case
        ({"field": "output", "operator": "not_matches", "value": r"warning"}, True),
        ({"field": "errors", "operator": "empty", "value": ""}, True),
        ({"field": "any_output", "operator": "not_empty", "value": ""}, True),
        ({"field": "exit_code", "operator": "is", "value": "0"}, True),
        ({"field": "exit_code", "operator": "not_one_of", "value": "0, 3010"}, False),
        ({"field": "exit_code", "operator": "gt", "value": "0"}, False),
        ({"field": "status", "operator": "is", "value": "Completed"}, True),
        ({"field": "status", "operator": "one_of", "value": "Failed, Timed out"}, False),
        ({"field": "value", "name": "bitlocker", "operator": "is", "value": "off"}, True),
        ({"field": "value", "name": "Missing", "operator": "is", "value": "off"}, False),
        ({"field": "value", "name": "Missing", "operator": "is_not", "value": "off"}, True),
        ({"field": "value", "name": "Missing", "operator": "empty", "value": ""}, True),
    ]
    for condition, expected in cases:
        assert rules.condition_matches(condition, result) is expected, condition
    # A timed-out run has no exit code: only "is not" style checks match it.
    assert rules.condition_matches({"field": "exit_code", "operator": "is_not", "value": "0"}, _result(exit_code=None))
    assert not rules.condition_matches({"field": "exit_code", "operator": "lt", "value": "1"}, _result(exit_code=None))

    found: dict = {}
    assert rules.rule_matches({"match_mode": "all", "conditions": [cases[0][0], cases[6][0]]}, result, found)
    assert found == {"match": "ERROR: disk", "groups": {"1": "disk"}}
    assert not rules.rule_matches({"match_mode": "all", "conditions": [cases[0][0], cases[1][0]]}, result)
    assert rules.rule_matches({"match_mode": "any", "conditions": [cases[1][0], cases[0][0]]}, result)
    assert not rules.rule_matches({"match_mode": "all", "conditions": []}, result)


def test_slow_regex_gives_up_instead_of_hanging():
    result = _result(output="a" * 50_000 + "!")
    assert not rules.condition_matches({"field": "output", "operator": "matches", "value": r"^(a+)+$"}, result)


def test_parse_conditions_checks_rows():
    parsed = rules.parse_conditions(
        ["output", "exit_code", "errors", "value", "output"], ["", "", "", "Status", "x"],
        ["matches", "one_of", "not_empty", "is", "contains"], [r"error \d+", "1, 2", "", "Bad", ""],
    )
    assert parsed == [
        {"field": "output", "name": "", "operator": "matches", "value": r"error \d+"},
        {"field": "exit_code", "name": "", "operator": "one_of", "value": "1, 2"},
        {"field": "errors", "name": "", "operator": "not_empty", "value": ""},
        {"field": "value", "name": "Status", "operator": "is", "value": "Bad"},
    ]
    bad = [
        (["output"], [""], ["matches"], ["(unclosed"], "not a valid regular expression"),
        (["output"], [""], ["gt"], ["lots"], "Enter a number"),
        (["exit_code"], [""], ["is"], ["zero"], "whole numbers"),
        (["exit_code"], [""], ["contains"], ["1"], "does not work with Exit code"),
        (["status"], [""], ["is"], ["Done"], "Completed, Failed or Timed out"),
        (["value"], [""], ["is"], ["x"], "name of the value"),
    ]
    for fields, names, operators, values, message in bad:
        with pytest.raises(rules.RuleError) as caught:
            rules.parse_conditions(fields, names, operators, values)
        assert message in caught.value.errors["condition_1"]
    with pytest.raises(rules.RuleError) as caught:
        rules.parse_conditions(["output"], [""], ["contains"], [""])
    assert "at least one condition" in caught.value.errors["conditions"]


@pytest.mark.anyio
async def test_save_rule_checks_scope_and_scripts(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    conditions = [{"field": "exit_code", "name": "", "operator": "is_not", "value": "0"}]
    with pytest.raises(rules.RuleError) as caught:
        await rules.save_rule(rule_id=None, form={"name": "", "scope": "global", "action": "run_script"},
                              conditions=conditions, **COMMON)
    assert set(caught.value.errors) == {"name", "scope", "next_script_id"}
    with pytest.raises(rules.RuleError) as caught:
        await rules.save_rule(rule_id=None, form={"name": "x", "action": "create_ticket",
                                                  "script_id": str(scripts["Theirs"]["id"])},
                              conditions=conditions, **COMMON)
    assert "script_id" in caught.value.errors  # another company's script

    form = {"name": "Fix it", "scope": "company", "action": "run_script", "is_enabled": "1", "match_mode": "any",
            "script_id": str(scripts["Check"]["id"]), "next_script_id": str(scripts["Fix"]["id"]),
            "pass_results": "1", "entry:param:Reason": "{{result.groups.1}}"}
    rule_id = await rules.save_rule(rule_id=None, form=form, conditions=conditions, **COMMON)
    saved = await rules_repo.get_rule(rule_id)
    assert "entries_encrypted" not in saved
    assert (saved["match_mode"], saved["pass_results"], saved["is_enabled"]) == ("any", True, True)
    assert saved["inputs"] == []  # values are read once the script's fields are shown
    await rules.save_rule(rule_id=rule_id, form=form, conditions=conditions, **COMMON)
    fields = rules.script_values(await rules_repo.get_rule(rule_id, with_secrets=True), scripts["Fix"])
    assert {field["key"]: field["value"] for field in fields}["param:Reason"] == "{{result.groups.1}}"
    listed = await rules_repo.list_rules_for_company(1, script_id=scripts["Fix"]["id"])
    assert listed == []  # watches Check, not Fix
    summary = rules.rule_summary((await rules_repo.list_rules_for_company(1))[0])
    assert summary["action_label"] == "Run Fix" and summary["joiner"] == "or"
    assert summary["conditions_text"] == ["Exit code is not “0”"]


@pytest.mark.anyio
async def test_matching_result_runs_the_next_script_with_the_result(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    _result_, agent = await _enrol(sqlite_db)
    form = {"name": "Low disk", "scope": "company", "action": "run_script", "is_enabled": "1",
            "script_id": str(scripts["Check"]["id"]), "next_script_id": str(scripts["Fix"]["id"]),
            "pass_results": "1", "entry:param:Reason": "{{result.groups.1}} at {{result.exit_code}}",
            "entry:param:Drive": "{{result.values.Drive Letter}}"}
    conditions = [{"field": "output", "name": "", "operator": "matches", "value": r"error: (\w+)"}]
    rule_id = await rules.save_rule(rule_id=None, form=form, conditions=conditions, **COMMON)
    await rules.save_rule(rule_id=rule_id, form=form, conditions=conditions, **COMMON)

    # No match: nothing else is queued.
    run_id = await _queue(scripts["Check"], agent)
    await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    await _finish(agent, run_id, {"exit_code": 0, "stdout": "all good"})
    assert len(await rmm_repo.list_runs(company_id=1)) == 1

    await sqlite_db.execute("INSERT INTO company_variable_definitions (id, name) VALUES (2, 'Drive Letter')")
    run_id = await _queue(scripts["Check"], agent)
    await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    outcome = await _finish(agent, run_id, {
        "exit_code": 2, "stdout": "ERROR: disk full", "stderr": "warn",
        "custom_values": [{"scope": "company", "name": "Drive Letter", "value": "C"}],
    })
    assert outcome["status"] == "failed"
    runs = await rmm_repo.list_runs(company_id=1)
    follow_up = next(run for run in runs if run["run_source"] == rules.RUN_SOURCE)
    assert follow_up["script_name"] == "Fix" and follow_up["asset_id"] == 10
    origin = await sqlite_db.fetch_one("SELECT parent_run_id, result_rule_id FROM rmm_script_runs WHERE id = %s",
                                       (follow_up["id"],))
    assert (origin["parent_run_id"], origin["result_rule_id"]) == (run_id, rule_id)
    job = (await rmm_scripts.wait_for_jobs(agent, wait_seconds=0))[0]
    assert job["parameters"] == {"Reason": "disk at 2", "Drive": "C"}
    assert job["env"]["MYPORTAL_PREVIOUS_OUTPUT"] == "ERROR: disk full"
    assert job["env"]["MYPORTAL_PREVIOUS_ERRORS"] == "warn"
    assert job["env"]["MYPORTAL_PREVIOUS_EXIT_CODE"] == "2"
    assert job["env"]["MYPORTAL_PREVIOUS_STATUS"] == "failed"
    assert job["env"]["MYPORTAL_PREVIOUS_RUN_ID"] == str(run_id)
    assert job["env"]["MYPORTAL_PREVIOUS_VALUES"] == '{"Drive Letter": "C"}'
    saved = await rules_repo.get_rule(rule_id)
    assert saved["match_count"] == 1 and "Ran Fix" in saved["last_run_summary"]


@pytest.mark.anyio
async def test_chains_stop_after_the_maximum_depth(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    _result_, agent = await _enrol(sqlite_db)
    # Any finished run of Check runs Check again: a loop the depth limit breaks.
    await rules.save_rule(rule_id=None, form={
        "name": "Again", "scope": "company", "action": "run_script", "is_enabled": "1",
        "script_id": str(scripts["Check"]["id"]), "next_script_id": str(scripts["Check"]["id"]),
    }, conditions=[{"field": "status", "name": "", "operator": "is", "value": "completed"}], **COMMON)
    run_id = await _queue(scripts["Check"], agent)
    for _ in range(rules.MAX_CHAIN_DEPTH + 2):
        jobs = await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
        if not jobs:
            break
        await _finish(agent, jobs[0]["id"], {"exit_code": 0, "stdout": "ok"})
    runs = await rmm_repo.list_runs(company_id=1)
    assert len(runs) == rules.MAX_CHAIN_DEPTH + 1
    rule = (await rules_repo.list_rules_for_company(1))[0]
    assert "Not run" in rule["last_run_summary"]
    assert run_id in [run["id"] for run in runs]


@pytest.mark.anyio
async def test_create_ticket_rule_includes_the_result_and_updates_the_open_ticket(sqlite_db, monkeypatch):
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service

    tickets: dict[int, dict] = {}
    replies: list[dict] = []

    async def create_ticket(**kwargs):
        ticket_id = len(tickets) + 100
        tickets[ticket_id] = {"id": ticket_id, "ticket_number": str(ticket_id), "status": "open", **kwargs}
        return tickets[ticket_id]

    async def get_ticket(ticket_id):
        return tickets.get(ticket_id)

    async def create_reply(**kwargs):
        replies.append(kwargs)
        return kwargs

    monkeypatch.setattr(tickets_service, "create_ticket", create_ticket)
    monkeypatch.setattr(tickets_repo, "get_ticket", get_ticket)
    monkeypatch.setattr(tickets_repo, "create_reply", create_reply)

    scripts = await _scripts(monkeypatch)
    _result_, agent = await _enrol(sqlite_db)
    rule_id = await rules.save_rule(rule_id=None, form={
        "name": "Check failed", "scope": "company", "action": "create_ticket", "is_enabled": "1",
        "ticket_priority": "high", "ticket_subject": "{{asset.name}} exit {{result.exit_code}}",
    }, conditions=[{"field": "exit_code", "name": "", "operator": "is_not", "value": "0"}], **COMMON)

    bad = "<script>alert(1)</script>"
    run_id = await _queue(scripts["Check"], agent)
    await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    await _finish(agent, run_id, {"exit_code": 3, "stdout": bad})
    ticket = tickets[100]
    assert ticket["subject"] == "PC-01 exit 3" and ticket["priority"] == "high"
    assert ticket["company_id"] == 1 and ticket["asset_ids"] == [10]
    assert bad not in ticket["description"] and "&lt;script&gt;" in ticket["description"]
    assert "Exit code:</strong> 3" in ticket["description"]

    run_id = await _queue(scripts["Fix"], agent)
    await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    await _finish(agent, run_id, {"exit_code": 1, "stdout": "again"})
    assert len(tickets) == 1 and replies[-1]["ticket_id"] == 100 and replies[-1]["is_internal"]
    assert "again" in replies[-1]["body"]
    assert "Updated ticket #100" in (await rules_repo.get_rule(rule_id))["last_run_summary"]

    # Built-in jobs never trigger rules.
    built_in = {"id": 999, "script_id": None, "company_id": 1, "asset_id": 10, "run_source": "patch"}
    assert await rules.apply(agent, built_in, _result(run_id=999, exit_code=1)) == 0
    assert (await rules_repo.get_rule(rule_id))["match_count"] == 2

    # A failing rule never loses the result.
    async def broken(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tickets_repo, "get_ticket", broken)
    run_id = await _queue(scripts["Check"], agent)
    await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    assert (await _finish(agent, run_id, {"exit_code": 1}))["status"] == "failed"
    assert "Could not act" in (await rules_repo.get_rule(rule_id))["last_run_summary"]


def test_rules_page_and_script_section_render():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    rule = {"id": 3, "name": "<b>Low disk</b>", "action": "run_script", "scope_label": "Contoso", "script_id": 1,
            "script_label": "Check", "joiner": "and", "action_label": "Run Fix", "pass_results": True,
            "conditions_text": ["Output contains “ERROR”"], "is_enabled": True, "match_count": 2,
            "last_matched_at": "2026-10-11T10:00:00", "last_run_summary": "Ran Fix on PC", "can_edit": True}
    html = env.get_template("rmm/script_result_rules.html").render(
        company={"name": "Contoso"}, rules=[rule], can_edit=True, is_super_admin=False,
        errors={"condition_1": "Bad regex."},
        editing={"id": 3, "name": "Low disk", "scope": "company", "script_id": 1, "match_mode": "all",
                 "action": "run_script", "next_script_id": 2, "timeout_seconds": 600, "pass_results": True,
                 "ticket_mode": "update", "ticket_subject": "", "ticket_priority": "normal", "is_enabled": True},
        conditions=[{"field": "output", "name": "", "operator": "matches", "value": "("}],
        fields=rules.FIELDS, operators=rules.OPERATORS, field_operators=rules.FIELD_OPERATORS, actions=rules.ACTIONS,
        match_modes=rules.MATCH_MODES, ticket_modes=rules.TICKET_MODES, ticket_priorities=rules.TICKET_PRIORITIES,
        max_chain_depth=rules.MAX_CHAIN_DEPTH,
        scripts=[{"id": 1, "name": "Check", "path": "rmm/Common/Check.ps1"}, {"id": 2, "name": "Fix", "path": "rmm/Common/Fix.ps1"}],
        next_script={"id": 2, "name": "Fix"},
        script_fields=[{"key": "param:Reason", "name": "Reason", "value": "{{result.output}}", "sensitive": False,
                        "mandatory": True, "default": None, "help": None, "value_set": True}],
        variables=[{"group": "Result", "label": "Output", "token": "{{result.output}}"}], csrf_token="tok",
    )
    assert "&lt;b&gt;Low disk&lt;/b&gt;" in html and "<b>Low disk</b>" not in html
    assert "[Add rule][Scripts]" in html and "Bad regex." in html
    assert 'name="entry:param:Reason"' in html and 'value="{{result.output}}"' in html
    assert 'value="global"' not in html
    assert 'action="/rmm/scripts/result-rules/3/enabled"' in html and 'name="pass_results"' in html

    macros = env.get_template("rmm/_macros.html").module
    assert "Script result rule" in str(macros.run_source({"run_source": "result_rule"}))
    source = env.loader.get_source(env, "rmm/_script_detail.html")[0]
    env.parse(source)
    assert "rmm.result_rule_menu(rule, csrf_token)" in source and "script_id={{ script.id }}" in source
