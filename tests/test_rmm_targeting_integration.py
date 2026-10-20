"""Advanced targeting through native RMM checks and company-scoped APIs."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.core.database import Database
from app.features.rmm import check_routes as routes
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_checks as checks_repo
from app.repositories import rmm_inventory as inventory_repo
from app.repositories import rmm_targeting as targeting_repo
from app.services import rmm_checks as checks
from app.services import rmm_targeting
from tests.test_rmm_scripts import _enrol, _sync, anyio_backend, sqlite_db  # noqa: F401 - shared fixtures

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024 ** 3


def condition(field, operator="equals", value=None, *, key=None):
    rule = {"field": field, "operator": operator}
    if operator not in {"is_set", "is_not_set"}:
        rule["value"] = value
    if key is not None:
        rule["key"] = key
    return rule


def group(operator, *children):
    return {"operator": operator, "children": list(children)}


@pytest.fixture
async def target_db(sqlite_db, monkeypatch):
    """Use the shared real check migrations and the native inventory tables."""
    await sqlite_db.conn.executescript(Database()._adapt_sql_for_sqlite(
        (ROOT / "migrations" / "477_rmm_device_inventory.sql").read_text()))
    columns = {row["name"] for row in await sqlite_db.fetch_all("PRAGMA table_info(companies)")}
    if "is_vip" not in columns:
        await sqlite_db.execute("ALTER TABLE companies ADD COLUMN is_vip INT DEFAULT 0")
    columns = {row["name"] for row in await sqlite_db.fetch_all("PRAGMA table_info(assets)")}
    for name, kind in (("type", "TEXT"), ("asset_type", "TEXT"), ("ram_gb", "REAL")):
        if name not in columns:
            await sqlite_db.execute(f"ALTER TABLE assets ADD COLUMN {name} {kind}")
    await sqlite_db.execute("CREATE TABLE IF NOT EXISTS ip_addresses (id INTEGER PRIMARY KEY, company_id INT, asset_id INT, address TEXT)")
    await sqlite_db.execute("CREATE TABLE IF NOT EXISTS asset_interfaces (id INTEGER PRIMARY KEY, company_id INT, asset_id INT, ip_address_id INT)")

    async def execute_many(sql, rows):
        await sqlite_db.conn.executemany(sqlite_db._sql(sql), list(rows))

    sqlite_db.execute_many = execute_many
    monkeypatch.setattr(targeting_repo, "db", sqlite_db)
    monkeypatch.setattr(inventory_repo, "db", sqlite_db)

    async def stats_unavailable():
        return False

    monkeypatch.setattr(checks.stats_db, "ensure_connected", stats_unavailable)
    return sqlite_db


async def inventory(fake, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/Check.ps1": "Write-Output 'healthy'\n"})
    script = (await rmm_repo.list_scripts())[0]
    _, first = await _enrol(fake)
    _, second = await _enrol(fake, tray_id=6, asset_id=11, uid="agent-uid-0002")
    _, other = await _enrol(fake, tray_id=7, asset_id=20, uid="agent-uid-0003")
    await fake.execute("UPDATE rmm_agents SET company_id = 2 WHERE id = %s", (other["id"],))
    other = await rmm_repo.get_agent(other["id"])
    await fake.execute("UPDATE companies SET is_vip = 1 WHERE id = 1")
    await fake.execute("UPDATE assets SET type = 'Server', asset_type = 'server' WHERE id IN (10, 20)")
    await fake.execute("UPDATE assets SET type = 'Workstation', asset_type = 'workstation' WHERE id = 11")
    await fake.execute("INSERT INTO asset_custom_field_values (asset_id, field_definition_id, value_boolean) VALUES (11, 2, 1)")
    await fake.execute("INSERT INTO ip_addresses (id, company_id, asset_id, address) VALUES (1, 1, 10, '10.40.1.12'), (2, 1, 11, '192.168.2.10'), (3, 2, 20, '10.40.1.20')")
    for agent, ram, software in ((first, 64, []), (second, 16, [{"name": "WireGuard", "version": "0.5.3"}]), (other, 32, [])):
        await inventory_repo.save_snapshot(
            agent, {"ram_bytes": ram * GIB, "os_version": "10.0.22631", "cpu_cores": 8},
            {"network_adapters": [{"name": "Ethernet", "addresses": ["10.99.0.5"]}]}, software,
            collected_at=datetime(2026, 10, 12),
        )
    return script, first, second, other


async def save(rules=None, *, check_id=None, company_id=1, super_admin=False, **form):
    payload = {"name": "Advanced check", "scope": "company", "check_type": "cpu", "cpu_threshold": "80",
               "duration_minutes": "0", "action": "none", "is_enabled": "1", **form}
    if rules is not None:
        payload.update(target_mode="rules", target_rules=rules)
    return await checks.save_check(check_id=check_id, company_id=company_id, form=payload,
                                   user_id=9, is_super_admin=super_admin)


def example_rules():
    return group("or",
                 group("and", condition("company.vip", value=True),
                       condition("asset.catalogue_type", value="server")),
                 group("and", condition("software.name", "contains", "wireguard"),
                       condition("asset.custom", value=True, key="Encrypted")))


@pytest.fixture
def api_client(monkeypatch):
    """Use real route handlers without starting unrelated background jobs."""
    async def audit(**_kwargs):
        return None

    monkeypatch.setattr(routes.audit_service, "record", audit)

    def make(*, super_admin=False, company_id=1, can_write=True):
        async def context(_request, *, write=False, api=False):
            if write and not can_write:
                raise HTTPException(status_code=403, detail="Permission to run scripts is required")
            return ({"id": 9, "is_super_admin": super_admin},
                    {"id": company_id, "name": "Contoso"}, company_id, can_write)

        monkeypatch.setattr(routes, "_context", context)
        app = FastAPI()
        app.include_router(routes.router)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    return make


@pytest.mark.anyio
async def test_nested_rules_persist_and_measurements_recheck_current_inventory(target_db, monkeypatch):
    _script, first, second, other = await inventory(target_db, monkeypatch)
    rules = example_rules()
    check_id = await save(rules, scope="global", super_admin=True)
    stored = await checks_repo.get_check(check_id)
    assert stored["target_rules"] == rmm_targeting.validate_rules(rules)
    raw = await target_db.fetch_one("SELECT target_rules_json FROM rmm_checks WHERE id = %s", (check_id,))
    assert json.loads(raw["target_rules_json"]) == stored["target_rules"]
    assert (await checks.agent_config(first))["cpu"] is True
    assert (await checks.agent_config(second))["cpu"] is True
    assert (await checks.agent_config(other))["cpu"] is False
    assert (await checks.ingest_report(first, {"cpu_percent": 95}))["checked"] == 1
    assert (await checks.ingest_report(second, {"cpu_percent": 95}))["checked"] == 1
    assert (await checks.ingest_report(other, {"cpu_percent": 95}))["checked"] == 0

    # Reports arriving after targeting data changes must not act on old config.
    await target_db.execute("UPDATE companies SET is_vip = 0 WHERE id = 1")
    await target_db.execute("UPDATE asset_custom_field_values SET value_boolean = 0 WHERE asset_id = 11")
    before = await checks_repo.get_state(check_id, first["id"])
    assert (await checks.agent_config(first))["cpu"] is False
    assert (await checks.ingest_report(first, {"cpu_percent": 10}))["checked"] == 0
    assert await checks_repo.get_state(check_id, first["id"]) == before
    assert (await checks.ingest_report(second, {"cpu_percent": 95}))["checked"] == 0


@pytest.mark.anyio
async def test_variables_counts_native_hardware_subnets_and_os_versions(target_db, monkeypatch):
    _script, first, second, other = await inventory(target_db, monkeypatch)
    rules = group("and", condition("company.variable", value="contoso", key="Tenant"),
                  condition("company.agent_count", "gte", 2), condition("asset.ram_gb", "gte", 32),
                  condition("asset.cpu_cores", "gte", 8),
                  condition("asset.ip_address", "in_subnet", "10.40.0.0/16"),
                  condition("asset.os_version", "version_gte", "10.0.19045"))
    check_id = await save(rules, scope="global", super_admin=True)
    assert [check["id"] for check in await checks.checks_for_agent(first)] == [check_id]
    assert await checks.checks_for_agent(second) == []
    assert await checks.checks_for_agent(other) == []
    await target_db.execute("UPDATE company_variable_values SET value = 'changed' WHERE company_id = 1")
    assert await checks.checks_for_agent(first) == []


@pytest.mark.anyio
async def test_targeting_filters_before_scope_overrides_including_disabled_checks(target_db, monkeypatch):
    _script, first, second, _other = await inventory(target_db, monkeypatch)
    broad = await save(scope="global", super_admin=True, name="Global CPU")
    server = await save(group("and", condition("asset.catalogue_type", value="server")), name="Server CPU")
    disabled = await save(group("and", condition("software.name", "contains", "WireGuard")),
                          scope="asset", asset_id="10", is_enabled="", name="Disable VPN CPU")
    assert [check["id"] for check in await checks.checks_for_agent(first)] == [server]
    assert [check["id"] for check in await checks.checks_for_agent(second)] == [broad]
    await target_db.execute("INSERT INTO rmm_device_software (agent_id, name) VALUES (%s, 'WireGuard')", (first["id"],))
    assert await checks.checks_for_agent(first) == []
    assert [check["id"] for check in await checks.checks_for_agent(first, include_off=True)] == [disabled]


@pytest.mark.anyio
@pytest.mark.parametrize("invalid", [
    {"operator": "and", "children": []},
    {"operator": "xor", "children": [{"field": "company.vip", "operator": "equals", "value": True}]},
    {"operator": "and", "children": [{"field": "company.credentials", "operator": "equals", "value": "x"}]},
    {"operator": "and", "children": [{"field": "asset.ip_address", "operator": "in_subnet", "value": "not-a-subnet"}]},
])
async def test_invalid_rule_tree_cannot_be_saved(target_db, invalid):
    with pytest.raises(checks.CheckError) as exc:
        await save(invalid)
    assert "target_rules" in exc.value.errors
    assert await checks_repo.list_checks_for_company(1) == []


@pytest.mark.anyio
@pytest.mark.parametrize("corrupt", ["broken-json", json.dumps(group("and")), json.dumps(group("and", condition("unsupported", value="x")))])
async def test_corrupted_persisted_rules_fail_closed(target_db, monkeypatch, corrupt):
    _script, first, _second, _other = await inventory(target_db, monkeypatch)
    check_id = await save(example_rules())
    await target_db.execute("UPDATE rmm_checks SET target_rules_json = %s WHERE id = %s", (corrupt, check_id))
    assert await checks.checks_for_agent(first) == []
    assert (await checks.ingest_report(first, {"cpu_percent": 95}))["checked"] == 0


@pytest.mark.anyio
async def test_offline_and_script_checks_recheck_targeting(target_db, monkeypatch):
    script, first, second, _other = await inventory(target_db, monkeypatch)
    await save(example_rules(), scope="global", super_admin=True, check_type="offline", duration_minutes="30")
    await target_db.execute("UPDATE rmm_agents SET last_seen_utc = NULL")
    assert await checks.evaluate_offline() == 2
    await save(example_rules(), scope="global", super_admin=True, check_type="script",
               check_script_id=str(script["id"]), script_expect="exit_code", script_value="0")
    calls = []

    async def run_script(**kwargs):
        calls.append(kwargs["agent"]["id"])
        return 100 + len(calls), "queued", True

    monkeypatch.setattr(checks.rmm_actions, "run_script", run_script)
    assert await checks.run_due_script_checks() == 2
    assert set(calls) == {first["id"], second["id"]}
    await target_db.execute("UPDATE companies SET is_vip = 0 WHERE id = 1")
    await target_db.execute("UPDATE asset_custom_field_values SET value_boolean = 0 WHERE asset_id = 11")
    assert await checks.evaluate_offline() == 0
    assert await checks.run_due_script_checks() == 0
    assert len(calls) == 2


@pytest.mark.anyio
async def test_delayed_actions_and_script_results_ignore_no_longer_matching_checks(target_db, monkeypatch):
    script, first, _second, _other = await inventory(target_db, monkeypatch)
    rules = group("and", condition("company.vip", value=True))
    delayed = await save(rules, action="schedule_script", script_id=str(script["id"]),
                         schedule_mode="delay", delay_minutes="1")
    state = {**checks._blank_state(), "status": "failing"}
    await checks_repo.save_state(delayed, first["id"], state)
    await checks_repo.schedule_action(delayed, first["id"], datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1))
    own_script = await save(rules, check_type="script", check_script_id=str(script["id"]),
                            script_expect="exit_code", script_value="0", action="run_script", script_id=str(script["id"]))
    await checks_repo.save_state(own_script, first["id"], {**checks._blank_state(), "run_id": 987})
    await target_db.execute("UPDATE companies SET is_vip = 0 WHERE id = 1")
    actions = []

    async def run_script(**kwargs):
        actions.append(kwargs)
        return 100, "queued", True

    async def get_run(_run_id):
        return {"id": 987, "status": "completed", "exit_code": 1, "stdout": "bad", "stderr": ""}

    monkeypatch.setattr(checks.rmm_actions, "run_script", run_script)
    monkeypatch.setattr(rmm_repo, "get_run", get_run)
    assert await checks.run_due_scheduled_checks() == 0
    assert await checks.run_finished(987) == 0
    assert actions == []


@pytest.mark.anyio
async def test_preview_company_scope_and_global_scope_security(target_db, monkeypatch, api_client):
    _script, first, second, other = await inventory(target_db, monkeypatch)
    rules = group("and", condition("agent.os", value="windows"))
    async with api_client() as client:
        response = await client.post("/api/rmm/checks/targeting/preview", json={"rules": rules})
        assert response.status_code == 200
        assert {agent["id"] for agent in response.json()["agents"]} == {first["id"], second["id"]}
        assert response.json()["matched_count"] == response.json()["eligible_count"] == 2
        assert "auth_token_hash" not in response.text and "details_json" not in response.text
        response = await client.post("/api/rmm/checks/targeting/preview", json={"rules": rules, "scope": "asset", "asset_id": 10})
        assert response.status_code == 200
        assert [agent["id"] for agent in response.json()["agents"]] == [first["id"]]
        assert response.json()["matched_count"] == response.json()["eligible_count"] == 1
        response = await client.post("/api/rmm/checks/targeting/preview", json={"rules": rules, "scope": "global"})
        assert response.status_code == 403
        response = await client.post("/api/rmm/checks/targeting/preview", json={"rules": rules, "scope": "asset", "asset_id": 20})
        assert response.status_code == 404
    async with api_client(super_admin=True) as client:
        response = await client.post("/api/rmm/checks/targeting/preview", json={"rules": rules, "scope": "global"})
        assert response.status_code == 200
        assert {agent["id"] for agent in response.json()["agents"]} == {first["id"], second["id"], other["id"]}
        assert response.json()["matched_count"] == response.json()["eligible_count"] == 3


@pytest.mark.anyio
async def test_preview_invalid_input_and_read_only_access(target_db, monkeypatch, api_client):
    await inventory(target_db, monkeypatch)
    async with api_client() as client:
        response = await client.post("/api/rmm/checks/targeting/preview", json={"rules": group("and")})
        assert response.status_code == 400
        assert "target_rules" in response.json()["errors"]
        assert (await client.post("/api/rmm/checks/targeting/preview", json={"rules": [], "scope": "company"})).status_code == 422
        assert (await client.post("/api/rmm/checks/targeting/preview", json={"rules": example_rules(), "scope": "invalid"})).status_code == 422
    async with api_client(can_write=False) as client:
        assert (await client.post("/api/rmm/checks/targeting/preview", json={"rules": example_rules()})).status_code == 403


@pytest.mark.anyio
async def test_form_roundtrip_rules_and_legacy_scope_compatibility(target_db, monkeypatch, api_client):
    await inventory(target_db, monkeypatch)
    rules = example_rules()
    payload = {"name": "VPN check", "scope": "company", "check_type": "cpu", "cpu_threshold": "80",
               "duration_minutes": "0", "action": "none", "is_enabled": "1",
               "target_mode": "rules", "target_rules": json.dumps(rules)}
    async with api_client() as client:
        response = await client.post("/rmm/checks", data=payload)
        assert response.status_code == 303
    check = (await checks_repo.list_checks_for_company(1))[0]
    assert check["target_rules"] == rmm_targeting.validate_rules(rules)
    assert routes.form_values(check)["target_mode"] == "rules"
    async with api_client(company_id=2) as client:
        assert (await client.post(f"/rmm/checks/{check['id']}", data=payload)).status_code == 404
    await save(check_id=check["id"], target_mode="all")
    assert (await checks_repo.get_check(check["id"]))["target_rules"] is None


@pytest.mark.anyio
async def test_unknown_native_inventory_does_not_match_absence_rules(target_db, monkeypatch):
    _script, first, second, _other = await inventory(target_db, monkeypatch)
    await target_db.execute("DELETE FROM rmm_device_inventory WHERE agent_id = %s", (second["id"],))
    await save(group("and", condition("software.name", "not_contains", "WireGuard")))
    assert len(await checks.checks_for_agent(first)) == 1
    assert await checks.checks_for_agent(second) == []


def test_deeply_nested_stored_json_and_posted_json_fail_closed():
    # The C JSON decoder's recursion guard differs from Python's call limit.
    depth = 10_000
    raw = "[" * depth + "0" + "]" * depth
    decoded = checks_repo._check_row({"id": 1, "target_rules_json": raw})
    assert decoded["target_rules"] is not None
    with pytest.raises(ValueError):
        rmm_targeting.validate_rules(decoded["target_rules"])
    posted = routes._posted({"target_mode": "rules", "target_rules": raw}, None)
    assert posted["target_rules"] == {}


@pytest.mark.anyio
@pytest.mark.parametrize("mode, error", [("unexpected", "target_mode"), ("rules", "target_rules")])
async def test_invalid_or_empty_rule_mode_cannot_create_unrestricted_check(target_db, mode, error):
    with pytest.raises(checks.CheckError) as exc:
        await save(target_mode=mode)
    assert error in exc.value.errors
    assert await checks_repo.list_checks_for_company(1) == []


@pytest.mark.anyio
async def test_asset_check_context_hides_checks_whose_rules_do_not_match(target_db, monkeypatch):
    _script, first, _second, _other = await inventory(target_db, monkeypatch)
    check_id = await save(group("and", condition("software.name", "contains", "WireGuard")))
    await checks_repo.save_state(check_id, first["id"], {**checks._blank_state(), "status": "failing"})

    async def stats(_asset_id, _range=None, _type=None, _hardware=None, **_kwargs):
        return {}

    monkeypatch.setattr(routes.rmm_stats, "asset_stats_context", stats)
    context = await routes.asset_checks_context(1, 10, can_edit=True, is_super_admin=False)
    assert context["checks"] == [] and context["failing"] == 0
    await target_db.execute("INSERT INTO rmm_device_software (agent_id, name) VALUES (%s, 'WireGuard')", (first["id"],))
    context = await routes.asset_checks_context(1, 10, can_edit=True, is_super_admin=False)
    assert context["checks"][0]["applies"] and not context["checks"][0]["replaced"]
    assert context["checks"][0]["state"]["status"] == "failing"


@pytest.mark.anyio
async def test_asset_page_lists_server_checks_only_on_servers(target_db, monkeypatch):
    await inventory(target_db, monkeypatch)
    await save(group("and", condition("asset.catalogue_type", value="server")), name="Server CPU")
    await save(name="Every device CPU", check_type="memory", memory_metric="free_percent", memory_threshold="10")
    # A workstation's own disk check replaces nothing on the server and still lists on the workstation.
    await save(name="Workstation disk", scope="asset", asset_id="11", check_type="disk", disk_drive="C:",
               disk_metric="free_percent", disk_operator="below", disk_threshold="10")

    async def stats(_asset_id, _range=None, _type=None, _hardware=None, **_kwargs):
        return {}

    monkeypatch.setattr(routes.rmm_stats, "asset_stats_context", stats)
    server = await routes.asset_checks_context(1, 10, can_edit=True, is_super_admin=False)
    workstation = await routes.asset_checks_context(1, 11, can_edit=True, is_super_admin=False)
    assert sorted(row["name"] for row in server["checks"]) == ["Every device CPU", "Server CPU"]
    assert sorted(row["name"] for row in workstation["checks"]) == ["Every device CPU", "Workstation disk"]
    assert all(row["applies"] for row in server["checks"] + workstation["checks"])
