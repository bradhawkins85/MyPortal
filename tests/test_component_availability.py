from __future__ import annotations

import asyncio

import pytest

from app.core.config import Settings
from app.services import modules as modules_service
from app.services.component_availability import (
    AvailabilityConfigurationError,
    ComponentAvailability,
    configure_component_availability,
    parse_slug_list,
)


@pytest.fixture(autouse=True)
def reset_availability():
    yield
    configure_component_availability(
        known_feature_packs=(),
        known_modules=(),
    )


def test_empty_configuration_preserves_availability():
    policy = configure_component_availability(
        disabled_feature_packs="",
        disabled_modules="",
        known_feature_packs=("trello",),
        known_modules=("trello",),
    )
    assert policy.feature_pack_available("trello")
    assert policy.module_available("trello")
    assert policy.module_enabled({"slug": "trello", "enabled": True})


def test_slug_parser_trims_and_deduplicates_in_order():
    assert parse_slug_list(" trello, xero,trello, , xero ") == ("trello", "xero")


def test_settings_parse_disabled_lists(monkeypatch):
    monkeypatch.setenv("DISABLED_FEATURE_PACKS", " trello,trello ")
    monkeypatch.setenv("DISABLED_MODULES", " xero, xero, trello ")
    settings = Settings()
    assert settings.disabled_feature_packs == "trello"
    assert settings.disabled_modules == "xero,trello"
    assert "trello" not in settings.feature_packs.split(",")


def test_settings_excludes_multiple_disabled_packs(monkeypatch):
    monkeypatch.setenv("DISABLED_FEATURE_PACKS", "trello,xero")
    settings = Settings()
    configured = settings.feature_packs.split(",")
    assert "trello" not in configured
    assert "xero" not in configured
    assert "tickets" in configured


def test_unknown_slugs_raise_actionable_configuration_error():
    with pytest.raises(AvailabilityConfigurationError) as exc_info:
        configure_component_availability(
            disabled_feature_packs="missing-pack",
            disabled_modules="missing-module",
            known_feature_packs=("trello",),
            known_modules=("trello",),
        )
    message = str(exc_info.value)
    assert "DISABLED_FEATURE_PACKS contains unknown slug(s): missing-pack" in message
    assert "DISABLED_MODULES contains unknown slug(s): missing-module" in message


def test_environment_overrides_database_enabled_flag():
    policy = ComponentAvailability(disabled_modules=frozenset({"trello"}))
    assert not policy.module_enabled({"slug": "trello", "enabled": True})


def test_environment_disabled_module_cannot_be_reenabled(monkeypatch):
    configure_component_availability(
        disabled_modules="trello",
        known_feature_packs=("trello",),
        known_modules=("trello",),
    )
    called = False

    async def update_module(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(modules_service.module_repo, "update_module", update_module)
    with pytest.raises(AvailabilityConfigurationError, match="disabled by deployment"):
        asyncio.run(modules_service.update_module("trello", enabled=True))
    assert not called


@pytest.mark.parametrize(
    ("policy", "pack", "module"),
    [
        (ComponentAvailability(disabled_modules=frozenset({"trello"})), False, False),
        (
            ComponentAvailability(disabled_feature_packs=frozenset({"trello"})),
            False,
            False,
        ),
    ],
)
def test_module_and_associated_pack_share_deployment_availability(policy, pack, module):
    assert policy.feature_pack_available("trello") is pack
    assert policy.module_available("trello") is module
