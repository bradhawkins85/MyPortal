from datetime import datetime
from pathlib import Path

import pytest

from app.core.notifications import DEFAULT_NOTIFICATION_EVENTS
from app.repositories import m365_policies as policy_repo
from app.services import m365_policies as policies
from app.services import m365_standard_library as library
from app.services import m365_standards as standards
from app.services.m365 import M365Error

NOW = datetime(2026, 10, 30, 4, 0, 0)
EARLIER = datetime(2026, 10, 29, 4, 0, 0)


def _mailbox(address, send_as=True, on_behalf=True, guid=None):
    return {"PrimarySmtpAddress": address, "ExchangeGuid": guid or address + "-guid",
            "MessageCopyForSentAsEnabled": send_as, "MessageCopyForSendOnBehalfEnabled": on_behalf}


@pytest.fixture
def exchange(monkeypatch):
    state = {"mailboxes": [], "calls": [], "fail_set": set(), "fail_get": False}

    async def token(company_id):
        return "token", "tenant"

    async def invoke(exo_token, tenant_id, cmdlet, parameters=None):
        state["calls"].append((cmdlet, parameters))
        if cmdlet == "Get-Mailbox":
            if state["fail_get"]:
                raise M365Error("403")
            return {"value": [dict(row) for row in state["mailboxes"]]}
        if parameters["Identity"] in state["fail_set"]:
            raise M365Error("denied")
        for row in state["mailboxes"]:
            if row["ExchangeGuid"] == parameters["Identity"]:
                row["MessageCopyForSentAsEnabled"] = parameters["MessageCopyForSentAsEnabled"]
                row["MessageCopyForSendOnBehalfEnabled"] = parameters["MessageCopyForSendOnBehalfEnabled"]
        return {}

    monkeypatch.setattr(library, "_acquire_exo_access_token", token)
    monkeypatch.setattr(library, "_exo_invoke_command", invoke)
    return state


@pytest.fixture
def recorded(monkeypatch):
    state = {"results": [], "alerts": [], "audits": [], "deleted": []}

    async def upsert(result):
        state["results"].append(result)

    async def emit(**kwargs):
        state["alerts"].append(kwargs)

    async def audit(**kwargs):
        state["audits"].append(kwargs)

    monkeypatch.setattr(policy_repo, "upsert_result", upsert)
    monkeypatch.setattr(policies.notifications_service, "emit_notification", emit)
    monkeypatch.setattr(policies.audit_service, "log_action", audit)
    return state


def _assignment(mode):
    return {"standard_id": library.DELEGATED_SENT_ITEMS, "mode": mode, "policy_id": 4,
            "policy_name": "Baseline", "settings": {"mailbox_types": ["UserMailbox", "SharedMailbox"]}}


def test_sent_items_standard_is_registered_with_all_modes():
    standard = standards.get(library.DELEGATED_SENT_ITEMS)
    assert standard is not None
    assert standard.modes == ("report", "alert", "remediate")
    assert standard.default_settings == {"mailbox_types": ["UserMailbox", "SharedMailbox"]}


def test_migration_and_drift_event_exist():
    sql = Path("migrations/534_m365_standard_results.sql").read_text(encoding="utf-8")
    assert sql.startswith("-- phase: expand")
    assert "CREATE TABLE IF NOT EXISTS m365_standard_results" in sql
    assert "PRIMARY KEY (company_id, standard_id)" in sql
    assert policies.DRIFT_EVENT in DEFAULT_NOTIFICATION_EVENTS


def test_scheduler_registers_standards_run():
    source = Path("app/services/scheduler.py").read_text(encoding="utf-8")
    assert 'id="m365-standards-run"' in source
    assert "m365_policies_service.run_standards()" in source


@pytest.mark.asyncio
async def test_check_passes_when_every_mailbox_keeps_copies(exchange):
    exchange["mailboxes"] = [_mailbox("a@x.com"), _mailbox("shared@x.com")]
    result = await library.check_delegated_sent_items(1, {})
    assert result["status"] == "pass"
    assert exchange["calls"][0] == ("Get-Mailbox", {
        "ResultSize": "Unlimited", "RecipientTypeDetails": ["UserMailbox", "SharedMailbox"]})


@pytest.mark.asyncio
async def test_check_lists_mailboxes_missing_either_setting(exchange):
    exchange["mailboxes"] = [_mailbox("a@x.com"), _mailbox("b@x.com", send_as=False),
                             _mailbox("c@x.com", on_behalf=None)]
    result = await library.check_delegated_sent_items(1, {"mailbox_types": ["SharedMailbox", "Bogus"]})
    assert result["status"] == "fail"
    assert result["affected"] == ["b@x.com", "c@x.com"]
    assert exchange["calls"][0][1]["RecipientTypeDetails"] == ["SharedMailbox"]


@pytest.mark.asyncio
async def test_check_is_unknown_when_exchange_cannot_be_read(exchange):
    exchange["fail_get"] = True
    assert (await library.check_delegated_sent_items(1, {}))["status"] == "unknown"
    exchange["fail_get"] = False
    assert (await library.check_delegated_sent_items(1, {}))["status"] == "unknown"


@pytest.mark.asyncio
async def test_apply_updates_only_drifted_mailboxes(exchange):
    exchange["mailboxes"] = [_mailbox("a@x.com"), _mailbox("b@x.com", send_as=False),
                             _mailbox("c@x.com", on_behalf=False)]
    exchange["fail_set"] = {"c@x.com-guid"}
    result = await library.apply_delegated_sent_items(1, {})
    assert result["status"] == "partial"
    updates = [params["Identity"] for cmdlet, params in exchange["calls"] if cmdlet == "Set-Mailbox"]
    assert updates == ["b@x.com-guid", "c@x.com-guid"]


@pytest.mark.asyncio
async def test_report_mode_records_drift_without_alerting(exchange, recorded):
    exchange["mailboxes"] = [_mailbox("b@x.com", send_as=False)]
    result = await policies.run_standard(1, "Acme", _assignment("report"), None, now=NOW)
    assert result["status"] == "drift"
    assert result["drift_since"] == NOW
    assert recorded["alerts"] == [] and recorded["audits"] == []
    assert not any(cmdlet == "Set-Mailbox" for cmdlet, _ in exchange["calls"])


@pytest.mark.asyncio
async def test_alert_mode_alerts_once_per_drift(exchange, recorded):
    exchange["mailboxes"] = [_mailbox("b@x.com", send_as=False)]
    first = await policies.run_standard(1, "Acme", _assignment("alert"), None, now=EARLIER)
    second = await policies.run_standard(1, "Acme", _assignment("alert"), first, now=NOW)
    assert len(recorded["alerts"]) == 1
    alert = recorded["alerts"][0]
    assert alert["event_type"] == "m365.standard_drift"
    assert alert["message"].startswith("Acme: Keep delegate-sent mail in Sent Items has drifted (policy Baseline).")
    assert second["drift_since"] == EARLIER

    exchange["mailboxes"] = [_mailbox("b@x.com")]
    cleared = await policies.run_standard(1, "Acme", _assignment("alert"), second, now=NOW)
    assert cleared["status"] == "compliant" and cleared["drift_since"] is None


@pytest.mark.asyncio
async def test_remediate_mode_fixes_and_audits(exchange, recorded):
    exchange["mailboxes"] = [_mailbox("b@x.com", send_as=False)]
    result = await policies.run_standard(1, "Acme", _assignment("remediate"), None, now=NOW)
    assert result["status"] == "remediated"
    assert result["remediated_at"] == NOW
    assert recorded["alerts"] == []
    assert recorded["audits"][0]["action"] == "m365.standard.remediate"
    assert recorded["audits"][0]["metadata"] == {"company_id": 1, "standard_id": library.DELEGATED_SENT_ITEMS,
                                                 "policy_id": 4}


@pytest.mark.asyncio
async def test_failed_remediation_alerts(exchange, recorded):
    exchange["mailboxes"] = [_mailbox("b@x.com", send_as=False)]
    exchange["fail_set"] = {"b@x.com-guid"}
    result = await policies.run_standard(1, "Acme", _assignment("remediate"), None, now=NOW)
    assert result["status"] == "failed"
    assert result["affected"] == ["b@x.com"]
    assert "could not be fixed" in recorded["alerts"][0]["message"]


@pytest.mark.asyncio
async def test_standard_errors_are_recorded_not_raised(monkeypatch, recorded):
    async def broken(company_id, settings):
        raise RuntimeError("boom")

    standards.register(standards.Standard("test_broken", "Broken", "Test", "", broken))
    try:
        result = await policies.run_standard(
            1, "Acme", {**_assignment("alert"), "standard_id": "test_broken"}, None, now=NOW)
    finally:
        standards.unregister("test_broken")
    assert result["status"] == "unknown"
    assert "boom" in result["details"]


@pytest.mark.asyncio
async def test_run_standards_clears_results_no_longer_assigned(monkeypatch, recorded):
    async def contexts(company_ids=None):
        return {1: {"company_id": 1, "company_name": "Acme"}}

    async def list_policies():
        return [{"id": 4, "name": "Baseline", "enabled": True, "target_rules": None, "overrides": [],
                 "standards": [{"standard_id": "test_ok", "mode": "report", "settings": {}}]}]

    async def list_results(company_id=None):
        return [{"company_id": 1, "standard_id": "old", "status": "drift"},
                {"company_id": 2, "standard_id": "test_ok", "status": "compliant"}]

    async def delete_stale(company_id, keep):
        recorded["deleted"].append((company_id, sorted(keep)))

    async def ok(company_id, settings):
        return {"status": "pass", "details": "fine"}

    monkeypatch.setattr(policies, "tenant_contexts", contexts)
    monkeypatch.setattr(policy_repo, "list_policies", list_policies)
    monkeypatch.setattr(policy_repo, "list_results", list_results)
    monkeypatch.setattr(policy_repo, "delete_stale_results", delete_stale)
    standards.register(standards.Standard("test_ok", "OK", "Test", "", ok))
    try:
        counts = await policies.run_standards()
    finally:
        standards.unregister("test_ok")
    assert counts == {"compliant": 1}
    assert recorded["deleted"] == [(1, ["test_ok"]), (2, [])]


@pytest.mark.asyncio
async def test_upsert_result_uses_dialect_specific_sql(monkeypatch):
    executed = []

    async def execute(sql, params=None):
        executed.append((sql, params))

    monkeypatch.setattr(policy_repo.db, "execute", execute)
    monkeypatch.setattr(policy_repo.db, "is_sqlite", lambda: True)
    await policy_repo.upsert_result({"company_id": 1, "standard_id": "s", "mode": "report", "status": "drift",
                                     "affected": ["a"], "checked_at": NOW})
    assert "ON CONFLICT(company_id, standard_id)" in executed[0][0]
    assert executed[0][1] == (1, "s", None, "report", "drift", None, '["a"]', NOW, None, None)
    monkeypatch.setattr(policy_repo.db, "is_sqlite", lambda: False)
    await policy_repo.upsert_result({"company_id": 1, "standard_id": "s", "mode": "report", "status": "drift",
                                     "checked_at": NOW})
    assert "ON DUPLICATE KEY UPDATE" in executed[1][0]
