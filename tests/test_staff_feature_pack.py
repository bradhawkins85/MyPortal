"""Smoke tests for the ``staff`` feature pack."""

from __future__ import annotations

from fastapi import FastAPI

import app.main as main_module
from app.core.features import init_registry
from app.features.staff import PACK
from app.features.staff import handlers as staff_handlers


EXPECTED = {
    ("GET", "/staff"),
    ("GET", "/staff/workflows/onboarding"),
    ("GET", "/staff/workflows/onboarding/policy"),
    ("POST", "/staff/workflows/onboarding/policy"),
    ("GET", "/staff/workflows/offboarding"),
    ("GET", "/staff/workflows/offboarding/policy"),
    ("POST", "/staff/workflows/offboarding/policy"),
    ("GET", "/staff/workflows/{direction}/policies"),
    ("POST", "/staff/workflows/{direction}/policies"),
    ("PUT", "/staff/workflows/{direction}/policies/{policy_id}"),
    ("DELETE", "/staff/workflows/{direction}/policies/{policy_id}"),
    ("GET", "/staff/workflows/history"),
    ("GET", "/staff/workflows/history/recent"),
    ("POST", "/api/staff/workflows/executions/{execution_id}/retry"),
    ("POST", "/staff"),
    ("PUT", "/staff/{staff_id}"),
    ("POST", "/api/staff/{staff_id}/offboarding/request"),
    ("DELETE", "/staff/{staff_id}"),
    ("POST", "/staff/enabled"),
    ("POST", "/staff/{staff_id}/verify"),
    ("POST", "/staff/{staff_id}/invite"),
    ("POST", "/api/staff/{staff_id}/m365/reset-password"),
    ("POST", "/api/staff/{staff_id}/m365/sign-in"),
}


def _routes_for(app: FastAPI) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app.router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path:
            continue
        for method in methods:
            routes.add((method, path))
    return routes


def test_staff_pack_manifest_declares_all_routes():
    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "staff"
    assert PACK.version
    assert declared == EXPECTED


def test_app_main_no_longer_owns_staff_routes():
    in_main_app = _routes_for(main_module.app)
    for method, path in EXPECTED:
        assert (method, path) not in in_main_app, (
            f"{method} {path} still mounted directly on app.main; "
            "feature-pack migration is incomplete."
        )


def test_staff_pack_owns_staff_handlers():
    for name in (
        "staff_page",
        "staff_onboarding_workflow_page",
        "staff_onboarding_workflow_policy",
        "upsert_staff_onboarding_workflow_policy",
        "staff_offboarding_workflow_page",
        "staff_offboarding_workflow_policy",
        "upsert_staff_offboarding_workflow_policy",
        "list_staff_workflow_policies",
        "create_staff_workflow_policy",
        "update_staff_workflow_policy",
        "delete_staff_workflow_policy",
        "staff_workflow_history_page",
        "staff_workflow_history_recent",
        "retry_workflow_execution",
        "create_staff_member",
        "update_staff_member",
        "request_staff_offboarding",
        "delete_staff_member",
        "set_staff_enabled",
        "verify_staff_member",
        "invite_staff_member",
        "m365_reset_staff_password",
        "m365_set_staff_sign_in",
    ):
        assert getattr(staff_handlers, name).__module__ == "app.features.staff.handlers"


def test_app_main_no_longer_defines_staff_handlers():
    for name in (
        "staff_page",
        "staff_onboarding_workflow_page",
        "staff_onboarding_workflow_policy",
        "upsert_staff_onboarding_workflow_policy",
        "staff_offboarding_workflow_page",
        "staff_offboarding_workflow_policy",
        "upsert_staff_offboarding_workflow_policy",
        "list_staff_workflow_policies",
        "create_staff_workflow_policy",
        "update_staff_workflow_policy",
        "delete_staff_workflow_policy",
        "staff_workflow_history_page",
        "staff_workflow_history_recent",
        "retry_workflow_execution",
        "create_staff_member",
        "update_staff_member",
        "request_staff_offboarding",
        "delete_staff_member",
        "set_staff_enabled",
        "verify_staff_member",
        "invite_staff_member",
        "m365_reset_staff_password",
        "m365_set_staff_sign_in",
    ):
        assert not hasattr(main_module, name), (
            f"app.main still defines {name}; "
            "feature-pack migration is incomplete."
        )


def test_staff_pack_loads_and_reloads_cleanly():
    import asyncio

    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("staff")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("staff")
        after_reload = _routes_for(test_app)
        assert EXPECTED.issubset(after_reload)

        counts: dict[tuple[str, str], int] = {}
        for route in test_app.router.routes:
            path = getattr(route, "path", None)
            for method in getattr(route, "methods", None) or set():
                if path:
                    counts[(method, path)] = counts.get((method, path), 0) + 1
        for key in EXPECTED:
            assert counts.get(key, 0) == 1, (
                f"Route {key} duplicated after reload (count={counts.get(key)})"
            )

        await registry.unload_all()

    asyncio.new_event_loop().run_until_complete(_run())
