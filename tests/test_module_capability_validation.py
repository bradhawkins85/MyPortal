"""Focused consistency tests for the integration capability registry."""
import pytest

from app.core import module_capabilities as registry
from app.services.modules import DEFAULT_MODULES


def _validate(**kwargs):
    return registry.validate_capability_registry(DEFAULT_MODULES, **kwargs)


def test_database_slugs_map_explicitly_to_python_feature_packs():
    assert registry.feature_pack_for_module("receive-sms") == "receive_sms"
    assert registry.feature_pack_for_module("m365-mail") == "m365_mail"
    assert registry.module_for_feature_pack("call_recordings") == "call-recordings"
    assert registry.feature_pack_for_module("receive_sms") is None


def test_capability_unknown_default_module_is_reported(monkeypatch):
    monkeypatch.setitem(registry.MODULE_CAPABILITIES, "missing", registry.ModuleCapabilities())
    assert any("unknown DEFAULT_MODULES slug: missing" in error for error in _validate())


def test_missing_configured_feature_pack_is_reported():
    assert any("does not exist: definitely_missing" in error for error in _validate(configured_feature_packs=["definitely_missing"]))


@pytest.mark.parametrize(
    ("argument", "value", "message"),
    [
        ("registered_commands", (), "scheduled command is not registered"),
        ("ui_keys", ("sidebar.unknown",), "sidebar/UI key has no known module"),
        ("guarded_routes", (), "route is mounted without the enabled dependency"),
        ("guarded_services", (), "external-call service lacks the common enabled guard"),
    ],
)
def test_capability_registration_categories_are_reported(argument, value, message):
    assert any(message in error for error in _validate(**{argument: value}))


def test_migration_active_defaults_do_not_override_existing_toggles():
    defaults = {module["slug"]: module for module in DEFAULT_MODULES}
    assert defaults["calls"]["enabled"] is True
    assert defaults["call-recordings"]["enabled"] is True
