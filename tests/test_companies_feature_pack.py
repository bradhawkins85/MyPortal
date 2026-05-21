"""Smoke tests for the ``companies`` feature pack."""

from __future__ import annotations

from fastapi import FastAPI

from app.core.features import init_registry
from app.features.companies import PACK


EXPECTED = {
    ("GET", "/admin/companies"),
    ("GET", "/admin/companies/{company_id}/edit"),
    ("POST", "/admin/companies"),
    ("POST", "/admin/companies/assign"),
    ("POST", "/admin/companies/{company_id}"),
    ("POST", "/admin/companies/{company_id}/staff-fields"),
    ("POST", "/admin/companies/{company_id}/staff-custom-fields"),
    ("POST", "/admin/companies/{company_id}/staff-custom-fields/{definition_id}"),
    (
        "POST",
        "/admin/companies/{company_id}/staff-custom-fields/{definition_id}/delete",
    ),
    ("POST", "/admin/companies/users/create"),
    ("POST", "/admin/companies/users/invite"),
    ("POST", "/admin/companies/assignment/{company_id}/{user_id}/permission"),
    ("POST", "/admin/companies/assignment/{company_id}/{user_id}/staff-permission"),
    ("POST", "/admin/companies/assignment/{company_id}/{user_id}/role"),
    ("POST", "/admin/companies/assignment/{company_id}/{staff_id}/pending/remove"),
    ("POST", "/admin/companies/assignment/{company_id}/{user_id}/remove"),
    ("POST", "/admin/companies/{company_id}/billing-contacts/add"),
    ("POST", "/admin/companies/{company_id}/billing-contacts/{staff_id}/remove"),
    ("GET", "/admin/companies/{company_id}/m365-provision"),
    ("GET", "/admin/companies/{company_id}/m365-discover"),
    ("POST", "/admin/companies/{company_id}/m365-credentials"),
    ("POST", "/admin/companies/{company_id}/m365-credentials/delete"),
    ("GET", "/admin/companies/{company_id}/tray"),
    ("POST", "/admin/companies/{company_id}/tray"),
    ("POST", "/admin/companies/{company_id}/tray/tokens"),
    ("POST", "/admin/companies/{company_id}/tray/tokens/{token_id}/revoke"),
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


def test_companies_pack_manifest_declares_all_routes():
    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "companies"
    assert PACK.version
    assert declared == EXPECTED


def test_companies_pack_loads_and_reloads_cleanly():
    import asyncio

    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("companies")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("companies")
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
