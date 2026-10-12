"""Event log rules: matching, saving, dropping entries and running scripts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_event_log_rules as rules_repo
from app.repositories import rmm_event_logs as event_log_repo
from app.services import rmm_event_log_rules as rules
from app.services import rmm_event_logs as event_logs
from app.services import rmm_scripts
from tests.test_rmm_event_logs import event_db  # noqa: F401 - fixture
from tests.test_rmm_scripts import _enrol, _environment, _sync, anyio_backend, sqlite_db  # noqa: F401 - fixtures

ALERT_SCRIPT = "param([string]$Message, [securestring]$Token)\nWrite-Output $Message\n"


def _entry(record_id, *, log="System", level=4, event_id=7036, provider="Service Control Manager",
           message="Service started"):
    return {
        "log_name": log, "record_id": record_id, "event_id": event_id, "level": level, "provider": provider,
        "computer": "PC-01", "message": message, "time_created": datetime.now(timezone.utc) - timedelta(minutes=1),
    }


async def _scripts(monkeypatch):
    await _sync(monkeypatch, {
        "rmm/Common/Alert.ps1": ALERT_SCRIPT,
        "rmm/Companies/Contoso/Local.ps1": "Write-Output local\n",
    })
    return {script["name"]: script for script in await rmm_repo.list_scripts()}


def test_conditions_match_every_field():
    entry = _entry(1, level=2, event_id=7, provider="Disk", message="The device \\Device\\Harddisk0 has a bad block")
    cases = [
        ({"field": "event_id", "operator": "is", "value": "7"}, True),
        ({"field": "event_id", "operator": "one_of", "value": "41, 7,6008"}, True),
        ({"field": "event_id", "operator": "not_one_of", "value": "7"}, False),
        ({"field": "level", "operator": "at_least", "value": "Warning"}, True),
        ({"field": "level", "operator": "at_least", "value": "critical"}, False),
        ({"field": "level", "operator": "is", "value": "Error"}, True),
        ({"field": "provider", "operator": "is", "value": "disk"}, True),
        ({"field": "message", "operator": "contains", "value": "BAD BLOCK"}, True),
        ({"field": "message", "operator": "not_contains", "value": "bad block"}, False),
        ({"field": "log_name", "operator": "starts_with", "value": "sys"}, True),
        ({"field": "computer", "operator": "is_not", "value": "PC-01"}, False),
    ]
    for condition, expected in cases:
        assert rules.condition_matches(condition, entry) is expected, condition
    assert rules.rule_matches({"conditions": [cases[0][0], cases[3][0]]}, entry)
    assert not rules.rule_matches({"conditions": [cases[0][0], cases[4][0]]}, entry)
    assert not rules.rule_matches({"conditions": []}, entry)  # an empty rule never matches
    # Windows level 0 ("log always") counts as information.
    assert rules.condition_matches({"field": "level", "operator": "is", "value": "Information"}, _entry(2, level=0))


def test_parse_conditions_checks_rows():
    parsed = rules.parse_conditions(["event_id", "message", "level"], ["is", "contains", "is"], [" 7036 ", "", "Error"])
    assert parsed == [{"field": "event_id", "operator": "is", "value": "7036"},
                      {"field": "level", "operator": "is", "value": "Error"}]
    with pytest.raises(rules.RuleError) as exc:
        rules.parse_conditions(["event_id"], ["is"], [""])
    assert "conditions" in exc.value.errors
    with pytest.raises(rules.RuleError) as exc:
        rules.parse_conditions(["event_id", "level", "message", "bogus"], ["contains", "is", "at_least", "is"],
                               ["7", "Loud", "x", "y"])
    assert set(exc.value.errors) == {"condition_1", "condition_2", "condition_3", "condition_4"}
    with pytest.raises(rules.RuleError) as exc:
        rules.parse_conditions(["event_id"], ["one_of"], ["7, x"])
    assert "condition_1" in exc.value.errors


@pytest.mark.anyio
async def test_save_rule_checks_scope_and_script(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    conditions = [{"field": "event_id", "operator": "is", "value": "7"}]

    async def save(form, *, rule_id=None, company_id=1, super_admin=False):
        return await rules.save_rule(rule_id=rule_id, company_id=company_id, form=form, conditions=conditions,
                                     user_id=9, is_super_admin=super_admin)

    with pytest.raises(rules.RuleError) as exc:
        await save({"name": "", "scope": "global", "action": "nope"})
    assert set(exc.value.errors) == {"name", "scope", "action"}
    with pytest.raises(rules.RuleError) as exc:
        await save({"name": "x", "scope": "asset", "asset_id": "20", "action": "drop"})
    assert "asset_id" in exc.value.errors  # another company's device
    with pytest.raises(rules.RuleError) as exc:
        await save({"name": "x", "scope": "global", "action": "run_script", "script_id": str(scripts["Local"]["id"])},
                   super_admin=True)
    assert "Common" in exc.value.errors["script"]

    drop_id = await save({"name": "Noise", "scope": "company", "action": "drop", "is_enabled": "1"})
    rule = await rules_repo.get_rule(drop_id)
    assert (rule["company_id"], rule["asset_id"], rule["script_id"], rule["is_enabled"]) == (1, None, None, True)
    assert rule["conditions"] == conditions and "entries_encrypted" not in rule

    # A newly chosen script's values are saved on the next save.
    form = {"name": "Alert", "scope": "asset", "asset_id": "10", "action": "run_script",
            "script_id": str(scripts["Alert"]["id"]), "is_enabled": "1",
            "entry:param:Message": "{{event.message}}", "entry:param:Token": "hunter2"}
    run_id = await save(form)
    assert rules.script_values(await rules_repo.get_rule(run_id, with_secrets=True), scripts["Alert"])[0]["value"] == ""
    await save(form, rule_id=run_id)
    await save({**form, "entry:param:Token": ""}, rule_id=run_id)  # a blank secret keeps the saved one
    stored = await rules_repo.get_rule(run_id, with_secrets=True)
    assert "hunter2" not in stored["entries_encrypted"]
    values = {field["key"]: field for field in rules.script_values(stored, scripts["Alert"])}
    assert values["param:Message"]["value"] == "{{event.message}}"
    assert values["param:Token"]["value"] == "" and values["param:Token"]["value_set"]

    global_id = await save({"name": "Everywhere", "scope": "global", "action": "drop"}, super_admin=True)
    every = await rules_repo.get_rule(global_id)
    assert not rules.can_edit(every, company_id=1, is_super_admin=False)
    assert rules.can_edit(every, company_id=1, is_super_admin=True)
    assert [r["name"] for r in await rules_repo.list_rules_for_company(1)] == ["Everywhere", "Noise", "Alert"]
    assert [r["name"] for r in await rules_repo.list_rules_for_company(2)] == ["Everywhere"]
    assert {r["name"] for r in await rules_repo.list_rules_for_device(1, 11)} == {"Everywhere", "Noise"}


@pytest.mark.anyio
async def test_ingest_drops_entries_and_runs_scripts_with_a_cooldown(sqlite_db, event_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    _result, agent = await _enrol(sqlite_db)
    common = {"user_id": 9, "is_super_admin": False, "company_id": 1}
    await rules.save_rule(rule_id=None, form={"name": "Noise", "scope": "company", "action": "drop", "is_enabled": "1"},
                          conditions=[{"field": "event_id", "operator": "is", "value": "7036"}], **common)
    other_device = await rules.save_rule(
        rule_id=None, form={"name": "Elsewhere", "scope": "asset", "asset_id": "11", "action": "drop", "is_enabled": "1"},
        conditions=[{"field": "log_name", "operator": "is", "value": "System"}], **common)
    await rules.save_rule(rule_id=None, form={"name": "Off", "scope": "company", "action": "drop"},
                          conditions=[{"field": "log_name", "operator": "is", "value": "System"}], **common)
    form = {"name": "Disk alert", "scope": "asset", "asset_id": "10", "action": "run_script", "is_enabled": "1",
            "script_id": str(scripts["Alert"]["id"]), "cooldown_minutes": "30",
            "entry:param:Message": "{{event.event_id}}: {{event.message}}"}
    conditions = [{"field": "level", "operator": "at_least", "value": "Error"}]
    alert_id = await rules.save_rule(rule_id=None, form=form, conditions=conditions, **common)
    await rules.save_rule(rule_id=alert_id, form=form, conditions=conditions, **common)

    batch = [_entry(1), _entry(2), _entry(3, level=2, event_id=7, message="Bad block"),
             _entry(4, level=1, event_id=41, message="Rebooted")]
    assert await event_logs.ingest(agent, batch) == {"received": 4, "stored": 2, "skipped": 0, "dropped": 2, "runs": 1}
    assert sorted(e["record_id"] for e in await event_log_repo.list_entries(company_id=1)) == [3, 4]

    runs = await rmm_repo.list_runs(company_id=1)
    assert len(runs) == 1 and runs[0]["run_source"] == rules.RUN_SOURCE and runs[0]["asset_id"] == 10
    jobs = await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    assert jobs[0]["parameters"]["Message"] == "7: Bad block"  # the first matching event

    alert = await rules_repo.get_rule(alert_id)
    assert alert["match_count"] == 2 and "Ran Alert" in alert["last_run_summary"]
    assert (await rules_repo.get_rule(other_device))["match_count"] == 0

    # Within the cooldown the rule counts matches but does not run again.
    stats = await event_logs.ingest(agent, [_entry(5, level=2, event_id=9)])
    assert (stats["runs"], stats["stored"]) == (0, 1)
    assert len(await rmm_repo.list_runs(company_id=1)) == 1
    assert (await rules_repo.get_rule(alert_id))["match_count"] == 3

    await sqlite_db.execute("UPDATE rmm_event_log_rule_triggers SET last_triggered_at = '2000-01-01 00:00:00'")
    assert (await event_logs.ingest(agent, [_entry(6, level=2, event_id=9)]))["runs"] == 1

    # A broken rule never loses the batch.
    async def broken(*_args, **_kwargs):
        raise RuntimeError("boom")

    await sqlite_db.execute("UPDATE rmm_event_log_rule_triggers SET last_triggered_at = '2000-01-01 00:00:00'")
    monkeypatch.setattr(rmm_scripts, "queue_for_agent", broken)
    assert (await event_logs.ingest(agent, [_entry(8, level=2, event_id=9)]))["stored"] == 1


def test_rules_page_and_asset_sections_render():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    rule = {"id": 3, "name": "<b>Noise</b>", "action": "drop", "scope_label": "Company: Contoso",
            "action_label": "Drop the event", "conditions_text": ["Event ID is “7036”"], "is_enabled": True,
            "match_count": 4, "last_matched_at": "2026-10-11T10:00:00", "last_run_summary": None,
            "cooldown_minutes": 60, "asset_id": None, "can_edit": True}
    html = env.get_template("rmm/event_log_rules.html").render(
        company={"name": "Contoso"}, rules=[rule], can_edit=True, is_super_admin=False, errors={"name": "Give it a name."},
        editing={"id": None, "name": "", "scope": "asset", "asset_id": 10, "action": "run_script", "script_id": 1,
                 "timeout_seconds": 600, "cooldown_minutes": 60, "is_enabled": True},
        conditions=[{"field": "event_id", "operator": "is", "value": ""}] * 3,
        fields=rules.FIELDS, operators=rules.OPERATORS, field_operators=rules.FIELD_OPERATORS, actions=rules.ACTIONS,
        devices=[{"asset_id": 10, "asset_name": "PC-01"}], scripts=[{"id": 1, "name": "Alert", "path": "rmm/Common/Alert.ps1"}],
        script={"id": 1, "name": "Alert"},
        script_fields=[{"key": "param:Message", "name": "Message", "value": "{{event.message}}", "sensitive": False,
                        "mandatory": False, "default": None, "help": None, "value_set": True}],
        variables=[{"group": "Event", "label": "Message", "token": "{{event.message}}"}], csrf_token="tok",
    )
    assert "&lt;b&gt;Noise&lt;/b&gt;" in html and "<b>Noise</b>" not in html
    assert "[Add rule][Event logs]" in html and "Give it a name." in html
    assert 'name="entry:param:Message"' in html and 'value="{{event.message}}"' in html
    assert 'value="global"' not in html  # only super admins add rules for every company
    assert 'action="/rmm/event-logs/rules/3/enabled"' in html and "/assets/10#event-log-rules" in html


@pytest.mark.anyio
async def test_asset_page_lists_the_device_logs_and_rules(sqlite_db, event_db):
    from pathlib import Path

    from app.features.rmm import event_log_routes as routes

    _result, agent = await _enrol(sqlite_db)
    await event_logs.ingest(agent, [_entry(1, level=2, event_id=7)])
    await event_logs.save_extra_logs("Security", asset_id=10)
    common = {"user_id": 9, "company_id": 1, "rule_id": None,
              "conditions": [{"field": "event_id", "operator": "is", "value": "1"}]}
    await rules.save_rule(form={"name": "Device", "scope": "asset", "asset_id": "10", "action": "drop"},
                          is_super_admin=False, **common)
    await rules.save_rule(form={"name": "Other device", "scope": "asset", "asset_id": "11", "action": "drop"},
                          is_super_admin=False, **common)
    await rules.save_rule(form={"name": "Global", "scope": "global", "action": "drop"}, is_super_admin=True, **common)

    context = await routes.asset_event_log_context(1, 10, can_edit=True, is_super_admin=False)
    assert [entry["event_id"] for entry in context["entries"]] == [7]
    assert context["extra_logs"] == ["Security"] and context["default_logs"] == ["Application", "System"]
    assert {rule["name"]: rule["can_edit"] for rule in context["rules"]} == {"Device": True, "Global": False}

    assert routes._back_path({"return_to": "asset"}, 10) == "/assets/10#event-logs"
    assert routes._back_path({"return_to": "//evil.example"}, 10) == routes.PAGE_PATH

    detail = (Path(__file__).parents[1] / "app/templates/assets/detail.html").read_text()
    assert 'id="event-logs"' in detail and 'id="event-log-rules"' in detail
    assert "/rmm/event-logs/rules?new=1&amp;asset_id={{ asset.id }}" in detail


@pytest.mark.anyio
async def test_drop_rule_run_now_deletes_matching_stored_events(sqlite_db, event_db):
    _result, agent = await _enrol(sqlite_db)
    _result, other = await _enrol(sqlite_db, tray_id=6, asset_id=11, uid="agent-uid-0002")
    await event_logs.ingest(agent, [_entry(1), _entry(2, event_id=7), _entry(3, message="Service stopped")])
    await event_logs.ingest(other, [_entry(4)])
    common = {"user_id": 9, "company_id": 1, "rule_id": None, "is_super_admin": False}
    conditions = [{"field": "event_id", "operator": "one_of", "value": "7036, 41"},
                  {"field": "message", "operator": "contains", "value": "started"}]
    device_id = await rules.save_rule(form={"name": "Device", "scope": "asset", "asset_id": "10", "action": "drop"},
                                      conditions=conditions, **common)
    company_id = await rules.save_rule(form={"name": "Company", "scope": "company", "action": "drop"},
                                       conditions=conditions, **common)
    device = await rules_repo.get_rule(device_id)
    company = await rules_repo.get_rule(company_id)

    assert rules._event_id_prefilter(device) == [41, 7036]
    assert await rules.drop_stored(device, delete=False) == 1  # PC-01's started event only
    assert await rules.drop_stored(company, delete=False) == 2
    assert len(await event_log_repo.list_entries(company_id=1)) == 4  # counting deletes nothing

    assert await rules.drop_stored(company, delete=True) == 2
    assert sorted(e["record_id"] for e in await event_log_repo.list_entries(company_id=1)) == [2, 3]
    assert "deleted 2 stored events" in (await rules_repo.get_rule(company_id))["last_run_summary"]
    assert await rules.drop_stored(device, delete=True) == 0

    script_rule = {**device, "action": "run_script"}
    with pytest.raises(rules.RuleError):
        await rules.drop_stored(script_rule, delete=False)


@pytest.mark.anyio
async def test_run_now_scans_in_batches(sqlite_db, event_db, monkeypatch):
    _result, agent = await _enrol(sqlite_db)
    await event_logs.ingest(agent, [_entry(index, message="Noise" if index % 2 else "Keep") for index in range(1, 12)])
    rule_id = await rules.save_rule(
        form={"name": "Noise", "scope": "company", "action": "drop"}, rule_id=None, company_id=1, user_id=9,
        is_super_admin=False, conditions=[{"field": "message", "operator": "is", "value": "noise"}])
    monkeypatch.setattr(rules, "RUN_NOW_BATCH", 3)
    assert await rules.drop_stored(await rules_repo.get_rule(rule_id), delete=True) == 6
    assert sorted(e["record_id"] for e in await event_log_repo.list_entries(company_id=1)) == [2, 4, 6, 8, 10]


def test_rules_page_run_now_confirm_and_action_menu():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    drop = {"id": 3, "name": "Noise", "action": "drop", "scope_label": "Company: Contoso", "action_label": "Drop the event",
            "conditions_text": ["Event ID is “7036”"], "is_enabled": True, "match_count": 0, "cooldown_minutes": 60,
            "asset_id": None, "can_edit": True}
    script = {**drop, "id": 4, "name": "Alert", "action": "run_script", "action_label": "Run Alert", "is_enabled": False}
    base = dict(company={"name": "Contoso"}, rules=[drop, script], can_edit=True, is_super_admin=False, errors={},
                editing=None, conditions=[], fields=rules.FIELDS, operators=rules.OPERATORS,
                field_operators=rules.FIELD_OPERATORS, actions=rules.ACTIONS, devices=[], scripts=[], script=None,
                script_fields=[], variables=[], csrf_token="tok")
    html = env.get_template("rmm/event_log_rules.html").render(
        **base, run_now={"rule": drop, "available": True, "count": 12, "asset_id": 10})
    assert 'action="/rmm/event-logs/rules/3/run"' in html and "12 stored events" in html
    assert 'name="asset_id" value="10"' in html and 'href="/assets/10#event-log-rules"' in html
    # One Action menu per rule; only drop rules can run now.
    assert html.count("data-rmm-menu") == 2
    assert "/rmm/event-logs/rules?run=3" in html and "/rmm/event-logs/rules?run=4" not in html
    assert "Turn off" in html and "Turn on" in html and 'name="_csrf" value="tok"' in html

    empty = env.get_template("rmm/event_log_rules.html").render(
        **base, run_now={"rule": drop, "available": True, "count": 0, "asset_id": None})
    assert "nothing to delete" in empty and "/rules/3/run" not in empty
    offline = env.get_template("rmm/event_log_rules.html").render(
        **base, run_now={"rule": drop, "available": False, "count": 0, "asset_id": None})
    assert "not available" in offline and "/rules/3/run" not in offline


def test_asset_page_template_uses_the_rule_menu():
    env = _environment()
    source = env.loader.get_source(env, "assets/detail.html")[0]
    env.parse(source)  # compiles
    assert "rmm_ui.event_log_rule_menu(rule, csrf_token, asset.id)" in source
