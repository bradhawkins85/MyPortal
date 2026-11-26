"""Tests for SNMP RMM checks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import ImmutableMultiDict

from app.features.rmm import check_routes
from app.security.encryption import decrypt_secret
from app.services import rmm_checks


def _form(**values):
    return ImmutableMultiDict([(key, str(value)) for key, value in values.items()])


def _snmp_check(**settings):
    base = {"oid": "1.3.6.1.2.1.33.1.2.4.0", "version": "v2c", "condition": "below", "value": "50", "port": 161}
    base.update(settings)
    return {"id": 1, "check_type": "snmp", "target": "ups01.local", "settings": base, "duration_minutes": 0}


def test_snmp_is_an_agent_measured_type():
    assert "snmp" in rmm_checks.CHECK_TYPES
    assert "snmp" in rmm_checks.AGENT_TYPES
    assert "snmp" not in rmm_checks.STATS_TYPES


def test_parse_snmp_v2c():
    errors: dict[str, str] = {}
    target, settings, _duration, errors = rmm_checks.parse_settings("snmp", _form(
        snmp_host="192.168.1.2", snmp_version="v2c", snmp_oid=".1.3.6.1.2.1.1.3.0", snmp_condition="responds",
        snmp_value="ignored", snmp_port="161", snmp_preset="uptime",
    ))
    assert errors == {}
    assert target == "192.168.1.2"
    assert settings["oid"] == "1.3.6.1.2.1.1.3.0"
    assert settings["value"] == ""
    assert "username" not in settings


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"snmp_host": "bad host!"}, "snmp_host"),
        ({"snmp_oid": "sysUpTime.0"}, "snmp_oid"),
        ({"snmp_oid": "1"}, "snmp_oid"),
        ({"snmp_version": "v4"}, "snmp_version"),
        ({"snmp_condition": "above", "snmp_value": "lots"}, "snmp_value"),
        ({"snmp_condition": "equals", "snmp_value": ""}, "snmp_value"),
        ({"snmp_port": "70000"}, "snmp_port"),
        ({"snmp_version": "v3", "snmp_username": ""}, "snmp_username"),
        ({"snmp_version": "v3", "snmp_username": "mon", "snmp_auth_protocol": "CRC"}, "snmp_auth_protocol"),
    ],
)
def test_parse_snmp_rejects_bad_fields(fields, error):
    values = {"snmp_host": "10.0.0.1", "snmp_oid": "1.3.6.1.2.1.1.3.0", "snmp_condition": "responds"}
    values.update(fields)
    _target, _settings, _duration, errors = rmm_checks.parse_settings("snmp", _form(**values))
    assert error in errors


def test_override_key_separates_oids_on_one_device():
    first = rmm_checks.override_key(_snmp_check(oid="1.3.6.1.2.1.1.3.0"))
    second = rmm_checks.override_key(_snmp_check(oid="1.3.6.1.2.1.33.1.2.4.0"))
    assert first != second
    assert rmm_checks.override_key(_snmp_check(oid=".1.3.6.1.2.1.1.3.0")) == first


def test_secrets_are_encrypted_and_kept_when_left_blank():
    errors: dict[str, str] = {}
    settings = {"version": "v2c"}
    encrypted = rmm_checks.snmp_secrets(settings, _form(snmp_community="s3cret-ro"), None, errors)
    assert errors == {}
    assert "s3cret-ro" not in encrypted
    assert json.loads(decrypt_secret(encrypted)) == {"snmp_community": "s3cret-ro"}

    existing = {"check_type": "snmp", "settings": {"version": "v2c", "secret_encrypted": encrypted}}
    kept = rmm_checks.snmp_secrets(settings, _form(snmp_community=""), existing, errors)
    assert errors == {}
    assert json.loads(decrypt_secret(kept)) == {"snmp_community": "s3cret-ro"}
    assert rmm_checks.snmp_secret_status(existing) == {
        "snmp_community": True, "snmp_auth_password": False, "snmp_priv_password": False,
    }


def test_v2c_needs_a_community_and_v3_needs_its_passwords():
    errors: dict[str, str] = {}
    assert rmm_checks.snmp_secrets({"version": "v2c"}, _form(), None, errors) is None
    assert "snmp_community" in errors

    errors = {}
    rmm_checks.snmp_secrets({"version": "v3", "security_level": "authPriv"},
                            _form(snmp_auth_password="short", snmp_priv_password=""), None, errors)
    assert set(errors) == {"snmp_auth_password", "snmp_priv_password"}

    errors = {}
    encrypted = rmm_checks.snmp_secrets(
        {"version": "v3", "security_level": "authNoPriv"},
        _form(snmp_auth_password="authpass1", snmp_priv_password="privpass1", snmp_community="x"), None, errors)
    assert errors == {}
    assert json.loads(decrypt_secret(encrypted)) == {"snmp_auth_password": "authpass1"}

    errors = {}
    assert rmm_checks.snmp_secrets({"version": "v3", "security_level": "noAuthNoPriv"}, _form(), None, errors) is None
    assert errors == {}


def test_agent_config_groups_oids_per_device_and_credentials():
    errors: dict[str, str] = {}
    encrypted = rmm_checks.snmp_secrets({"version": "v2c"}, _form(snmp_community="public"), None, errors)
    checks = [
        _snmp_check(secret_encrypted=encrypted),
        {**_snmp_check(oid="1.3.6.1.2.1.33.1.2.2.0", secret_encrypted=encrypted), "id": 2},
        {**_snmp_check(oid="1.3.6.1.2.1.33.1.2.2.0", secret_encrypted=encrypted), "id": 3},
        {**_snmp_check(secret_encrypted=encrypted), "id": 4, "target": "switch01"},
    ]
    config = rmm_checks.agent_config_for(checks)
    assert config["enabled"] is True
    by_host = {target["host"]: target for target in config["snmp"]}
    assert by_host["ups01.local"]["oids"] == ["1.3.6.1.2.1.33.1.2.4.0", "1.3.6.1.2.1.33.1.2.2.0"]
    assert by_host["ups01.local"]["community"] == "public"
    assert by_host["switch01"]["oids"] == ["1.3.6.1.2.1.33.1.2.4.0"]


def test_agent_config_sends_v3_credentials():
    errors: dict[str, str] = {}
    settings = {"version": "v3", "security_level": "authPriv"}
    encrypted = rmm_checks.snmp_secrets(settings, _form(snmp_auth_password="authpass1", snmp_priv_password="privpass1"),
                                        None, errors)
    check = _snmp_check(version="v3", username="mon", security_level="authPriv", auth_protocol="SHA256",
                        priv_protocol="AES", secret_encrypted=encrypted)
    target = rmm_checks.agent_config_for([check])["snmp"][0]
    assert target["auth_password"] == "authpass1" and target["priv_password"] == "privpass1"
    assert target["username"] == "mon" and target["auth_protocol"] == "SHA256"
    assert "community" not in target


@pytest.mark.parametrize(
    ("settings", "value", "problem"),
    [
        ({"condition": "below", "value": "50"}, "35", True),
        ({"condition": "below", "value": "50"}, "100", False),
        ({"condition": "above", "value": "0"}, "120", True),
        ({"condition": "above", "value": "0"}, "0", False),
        ({"condition": "equals", "value": "1"}, "1", False),
        ({"condition": "equals", "value": "1"}, "2", True),
        ({"condition": "equals", "value": "1.0"}, "1", False),
        ({"condition": "not_equals", "value": "down"}, "DOWN", True),
        ({"condition": "contains", "value": "ok"}, "Status OK", False),
        ({"condition": "not_contains", "value": "fault"}, "Fan fault", True),
        ({"condition": "responds", "value": ""}, "123456", False),
        ({"condition": "below", "value": "50"}, "not-a-number", True),
    ],
)
def test_evaluate_compares_values(settings, value, problem):
    check = _snmp_check(**settings)
    report = {"snmp": [{"host": "UPS01.local", "port": 161, "oid": ".1.3.6.1.2.1.33.1.2.4.0", "value": value}]}
    outcome = rmm_checks.evaluate(check, report)
    assert outcome is not None
    assert outcome[0] is problem
    assert value in outcome[1] or outcome[1].endswith("(not a number)")


def test_evaluate_fails_when_the_device_does_not_answer():
    report = {"snmp": [{"host": "ups01.local", "port": 161, "oid": "1.3.6.1.2.1.33.1.2.4.0",
                        "error": "request timeout (after 1 retries)"}]}
    problem, text = rmm_checks.evaluate(_snmp_check(condition="responds", value=""), report)
    assert problem is True and "No SNMP answer" in text


def test_evaluate_waits_when_the_report_has_nothing_for_the_check():
    assert rmm_checks.evaluate(_snmp_check(), {"snmp": []}) is None
    assert rmm_checks.evaluate(_snmp_check(port=1161), {"snmp": [
        {"host": "ups01.local", "port": 161, "oid": "1.3.6.1.2.1.33.1.2.4.0", "value": "1"}]}) is None


def test_describe_reads_like_the_form_summary():
    assert rmm_checks.describe(_snmp_check()) == "SNMP 1.3.6.1.2.1.33.1.2.4.0 on ups01.local is below 50"
    assert rmm_checks.describe(_snmp_check(condition="responds", value="", port=1161)).startswith(
        "ups01.local:1161 does not answer SNMP")


@pytest.mark.asyncio
async def test_save_requires_a_device_or_rules(monkeypatch):
    form = _form(name="UPS battery", check_type="snmp", scope="company", snmp_host="10.0.0.9",
                 snmp_oid="1.3.6.1.2.1.33.1.2.4.0", snmp_condition="below", snmp_value="50",
                 snmp_version="v2c", snmp_community="public", action="none")
    with pytest.raises(rmm_checks.CheckError) as excinfo:
        await rmm_checks.save_check(check_id=None, company_id=5, form=form, user_id=1, is_super_admin=False)
    assert "scope" in excinfo.value.errors


@pytest.mark.asyncio
async def test_save_on_a_device_stores_encrypted_community(monkeypatch):
    from app.repositories import assets as assets_repo
    from app.repositories import rmm_checks as checks_repo

    monkeypatch.setattr(assets_repo, "get_asset_by_id", AsyncMock(return_value={"id": 3, "company_id": 5}))
    create = AsyncMock(return_value=10)
    monkeypatch.setattr(checks_repo, "create_check", create)
    form = _form(name="UPS battery", check_type="snmp", scope="asset", asset_id="3", snmp_host="10.0.0.9",
                 snmp_oid="1.3.6.1.2.1.33.1.2.4.0", snmp_condition="below", snmp_value="50",
                 snmp_version="v2c", snmp_community="private-ro", action="none", is_enabled="1")
    assert await rmm_checks.save_check(check_id=None, company_id=5, form=form, user_id=1, is_super_admin=False) == 10
    values = create.await_args.args[0]
    assert values["target"] == "10.0.0.9"
    assert "private-ro" not in json.dumps(values["settings"])
    assert json.loads(decrypt_secret(values["settings"]["secret_encrypted"])) == {"snmp_community": "private-ro"}


def test_form_values_never_return_secrets():
    errors: dict[str, str] = {}
    encrypted = rmm_checks.snmp_secrets({"version": "v2c"}, _form(snmp_community="private-ro"), None, errors)
    values = check_routes.form_values({**_snmp_check(secret_encrypted=encrypted), "name": "UPS"})
    assert values["snmp_host"] == "ups01.local"
    assert values["snmp_saved"]["snmp_community"] is True
    assert "private-ro" not in json.dumps(values, default=str)
    assert encrypted not in json.dumps(values, default=str)


def test_posted_form_does_not_echo_secrets():
    posted = check_routes._posted(_form(check_type="snmp", snmp_community="private-ro", snmp_host="x"), None)
    assert "private-ro" not in json.dumps(posted, default=str)


def test_agent_report_model_accepts_snmp_results():
    report = check_routes.AgentCheckReport.model_validate({
        "snmp": [{"host": "10.0.0.9", "port": 161, "oid": "1.3.6.1.2.1.1.3.0", "value": "42", "type": "number"}],
    })
    assert report.model_dump()["snmp"][0]["value"] == "42"
    assert check_routes.AgentCheckReport.model_validate({"snmp": None}).snmp == []


def test_form_template_has_password_fields_without_values():
    source = Path("app/templates/rmm/_check_form.html").read_text(encoding="utf-8")
    for field in rmm_checks.SNMP_SECRET_FIELDS:
        tag = source[source.index(f'name="{field}"') - 200: source.index(f'name="{field}"') + 200]
        assert 'type="password"' in tag and 'value=""' in tag
