"""Smoke tests for the ``backups`` feature pack."""

from __future__ import annotations

from fastapi import FastAPI

import app.main as main_module
from app.core.features import init_registry
from app.features.backups import PACK
from app.features.backups import handlers as backup_handlers
from app.features.backups import routes as backup_routes


EXPECTED = {
    ("HEAD", "/admin/backup-jobs"),
    ("GET", "/admin/backup-jobs"),
    ("HEAD", "/admin/backup-summary"),
    ("GET", "/admin/backup-summary"),
    ("POST", "/admin/backup-jobs"),
    ("POST", "/admin/backup-jobs/{job_id}"),
    ("POST", "/admin/backup-jobs/{job_id}/delete"),
    ("POST", "/admin/backup-jobs/{job_id}/regenerate-token"),
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


def test_backups_pack_manifest_declares_all_routes():
    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "backups"
    assert PACK.version
    assert declared == EXPECTED


def test_app_main_no_longer_owns_backups_routes():
    in_main_app = _routes_for(main_module.app)
    for method, path in EXPECTED:
        assert (method, path) not in in_main_app, (
            f"{method} {path} still mounted directly on app.main; "
            "feature-pack migration is incomplete."
        )


def test_backups_pack_owns_handlers():
    assert backup_routes.router.routes[0].endpoint == backup_handlers.admin_backup_jobs_page
    assert backup_routes.router.routes[1].endpoint == backup_handlers.admin_backup_summary_page
    assert backup_routes.router.routes[2].endpoint == backup_handlers.admin_create_backup_job
    assert backup_routes.router.routes[3].endpoint == backup_handlers.admin_update_backup_job
    assert backup_routes.router.routes[4].endpoint == backup_handlers.admin_delete_backup_job
    assert (
        backup_routes.router.routes[5].endpoint
        == backup_handlers.admin_regenerate_backup_job_token
    )


def test_backups_pack_loads_and_reloads_cleanly():
    import asyncio

    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("backups")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("backups")
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
