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


def test_backup_summary_uses_menu_permission_and_scopes_non_super_admin(monkeypatch):
    import asyncio

    from starlette.requests import Request

    from app.repositories import companies as company_repo
    from app.services import backup_jobs as backup_jobs_service

    calls: dict[str, object] = {}

    async def fake_require_menu_page_access(request, key, *, write=False, detail=""):
        calls["permission"] = (key, write, detail)
        request.state.active_company_id = 10
        return {"id": 7, "company_id": 10, "is_super_admin": False}, None

    async def fake_list_jobs_with_latest(*, company_id=None, include_inactive=False):
        calls["jobs_company_id"] = company_id
        calls["jobs_include_inactive"] = include_inactive
        return [{"id": 1, "company_id": company_id, "today_status": "success"}]

    def fake_summarise_jobs(jobs):
        calls["summarised_jobs"] = jobs
        return {"total": len(jobs)}

    async def fake_list_companies():
        return [{"id": 10, "name": "Allowed"}, {"id": 20, "name": "Hidden"}]

    async def fake_build_history_grid(*, company_id=None, days=14, include_inactive=False):
        calls["history"] = (company_id, days, include_inactive)
        return []

    async def fake_render_template(template, request, user, *, extra=None):
        calls["render"] = (template, user, extra)
        return extra

    monkeypatch.setattr(main_module, "_require_menu_page_access", fake_require_menu_page_access)
    monkeypatch.setattr(main_module, "_render_template", fake_render_template)
    monkeypatch.setattr(backup_jobs_service, "list_jobs_with_latest", fake_list_jobs_with_latest)
    monkeypatch.setattr(backup_jobs_service, "summarise_jobs", fake_summarise_jobs)
    monkeypatch.setattr(backup_jobs_service, "build_history_grid", fake_build_history_grid)
    monkeypatch.setattr(company_repo, "list_companies", fake_list_companies)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/backup-summary",
        "query_string": b"company_id=20",
        "headers": [],
        "server": ("testserver", 80),
        "scheme": "http",
    }
    request = Request(scope)

    extra = asyncio.run(backup_handlers.admin_backup_summary_page(request))

    assert calls["permission"] == (
        "menu.admin.backup_summary",
        False,
        "Backup summary access required",
    )
    assert calls["jobs_company_id"] == 10
    assert calls["jobs_include_inactive"] is True
    assert calls["history"] == (10, 14, True)
    assert extra["backup_company_filter"] == 10
    assert extra["backup_companies"] == [{"id": 10, "name": "Allowed"}]
    assert extra["backup_company_lookup"] == {10: "Allowed"}


def test_backup_summary_keeps_super_admin_company_filter(monkeypatch):
    import asyncio

    from starlette.requests import Request

    from app.repositories import companies as company_repo
    from app.services import backup_jobs as backup_jobs_service

    calls: dict[str, object] = {}

    async def fake_require_menu_page_access(request, key, *, write=False, detail=""):
        return {"id": 1, "is_super_admin": True}, None

    async def fake_list_jobs_with_latest(*, company_id=None, include_inactive=False):
        calls["jobs_company_id"] = company_id
        return []

    async def fake_list_companies():
        return [{"id": 10, "name": "Allowed"}, {"id": 20, "name": "Other"}]

    async def fake_build_history_grid(*, company_id=None, days=14, include_inactive=False):
        calls["history_company_id"] = company_id
        return []

    async def fake_render_template(template, request, user, *, extra=None):
        return extra

    monkeypatch.setattr(main_module, "_require_menu_page_access", fake_require_menu_page_access)
    monkeypatch.setattr(main_module, "_render_template", fake_render_template)
    monkeypatch.setattr(backup_jobs_service, "list_jobs_with_latest", fake_list_jobs_with_latest)
    monkeypatch.setattr(backup_jobs_service, "summarise_jobs", lambda jobs: {"total": len(jobs)})
    monkeypatch.setattr(backup_jobs_service, "build_history_grid", fake_build_history_grid)
    monkeypatch.setattr(company_repo, "list_companies", fake_list_companies)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/backup-summary",
        "query_string": b"company_id=20",
        "headers": [],
        "server": ("testserver", 80),
        "scheme": "http",
    }

    extra = asyncio.run(backup_handlers.admin_backup_summary_page(Request(scope)))

    assert calls["jobs_company_id"] == 20
    assert calls["history_company_id"] == 20
    assert extra["backup_company_filter"] == 20
    assert extra["backup_companies"] == [{"id": 10, "name": "Allowed"}, {"id": 20, "name": "Other"}]
