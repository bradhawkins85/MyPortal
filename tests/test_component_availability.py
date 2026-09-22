from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.services import module_dispatch, modules as modules_service
from app.services.component_availability import (
    AvailabilityConfigurationError,
    ComponentAvailability,
    configure_component_availability,
    deployment_disabled_result,
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


@pytest.mark.anyio
async def test_default_sync_does_not_create_unavailable_module(monkeypatch):
    configure_component_availability(
        disabled_modules="trello",
        known_feature_packs=("trello",),
        known_modules=(module["slug"] for module in modules_service.DEFAULT_MODULES),
    )
    monkeypatch.setattr(modules_service.db, "is_connected", lambda: True)
    monkeypatch.setattr(modules_service.module_repo, "list_modules", lambda: asyncio.sleep(0, result=[]))
    created: list[str] = []

    async def upsert_module(**values):
        created.append(values["slug"])

    monkeypatch.setattr(modules_service.module_repo, "upsert_module", upsert_module)
    await modules_service.ensure_default_modules()
    assert "trello" not in created


@pytest.mark.anyio
async def test_dispatch_rejects_unavailable_module_before_handler(monkeypatch):
    configure_component_availability(
        disabled_modules="trello",
        known_feature_packs=("trello",),
        known_modules=("trello",),
    )
    handler = AsyncMock()
    monkeypatch.setattr(module_dispatch, "_trigger_module_handler", handler)

    result = await module_dispatch.trigger_module("trello", {"card_id": "secret"})

    assert result == {
        "status": "skipped",
        "reason": "Component disabled by deployment configuration",
        "module": "trello",
        "retryable": False,
    }
    handler.assert_not_awaited()


@pytest.mark.anyio
async def test_unavailable_internal_action_is_not_listed_or_dispatched(monkeypatch):
    configure_component_availability(
        disabled_modules="reprocess-ai",
        known_feature_packs=("reprocess_ai",),
        known_modules=(module["slug"] for module in modules_service.DEFAULT_MODULES),
    )
    monkeypatch.setattr(
        modules_service.module_repo,
        "list_modules",
        lambda: asyncio.sleep(0, result=[]),
    )
    actions = await modules_service.list_trigger_action_modules()
    assert "reprocess-ai" not in {item["slug"] for item in actions}
    assert await modules_service.trigger_module("reprocess-ai", {}) == {
        "status": "skipped",
        "reason": "Component disabled by deployment configuration",
        "module": "reprocess-ai",
        "retryable": False,
    }


@pytest.mark.anyio
async def test_unavailable_modules_are_absent_from_catalogue_and_lookup(monkeypatch):
    configure_component_availability(
        disabled_modules="trello",
        known_feature_packs=("trello",),
        known_modules=("trello", "xero"),
    )
    rows = [
        {"slug": "trello", "enabled": True, "settings": {}},
        {"slug": "xero", "enabled": True, "settings": {}},
    ]
    monkeypatch.setattr(modules_service.module_repo, "list_modules", lambda: asyncio.sleep(0, result=rows))
    monkeypatch.setattr(
        modules_service.module_repo, "get_module",
        lambda slug: asyncio.sleep(0, result=next((row for row in rows if row["slug"] == slug), None)),
    )
    assert [module["slug"] for module in await modules_service.list_modules()] == ["xero"]
    assert await modules_service.get_module("trello") is None


def test_deployment_skip_result_is_explicit_and_terminal():
    assert deployment_disabled_result("trello") == {
        "status": "skipped",
        "reason": "Component disabled by deployment configuration",
        "module": "trello",
        "retryable": False,
    }


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


def test_shared_capability_remains_available_with_an_available_owner():
    policy = ComponentAvailability(disabled_modules=frozenset({"xero"}))
    assert policy.command_available("refresh_company_ids")
    assert not policy.service_available("xero.api")


def test_module_without_feature_pack_deactivates_owned_capabilities():
    policy = ComponentAvailability(disabled_modules=frozenset({"smtp2go"}))
    assert not policy.route_available("webhooks.smtp2go")
    assert not policy.service_available("smtp2go.delivery")
    assert not policy.ui_feature_available("modules.smtp2go")
