"""Web-triggered upgrades: request queueing, progress tracking and host jobs."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.params import Depends

from app.api.dependencies.auth import require_super_admin
from app.api.routes.scheduler import check_system_update, request_system_update
from app.services import system_update_history, system_updates

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def history(monkeypatch, tmp_path):
    history_dir = tmp_path / "history"
    monkeypatch.setattr(system_update_history, "_HISTORY_DIR", history_dir)
    monkeypatch.setattr(system_updates, "_FLAG_PATH", tmp_path / "state" / "system_update.flag")
    return history_dir


def _docker(monkeypatch, *, installed="v1.0.0", latest="v1.1.0"):
    monkeypatch.setenv("MYPORTAL_DEPLOYMENT", "docker")
    monkeypatch.setattr(system_updates, "_installed_version", lambda: installed)

    async def fake_latest():
        return latest

    monkeypatch.setattr(system_updates, "_latest_release_tag", fake_latest)
    system_updates._check_cache.update(at=0.0, value=None)


def test_late_progress_report_never_reopens_a_finished_update(history):
    record = system_update_history.create_pending(
        requested_at="2026-09-29T00:00:00+00:00", target_revision="v1.1.0",
        source="web", mode="docker",
    )
    assert record["mode"] == "docker"
    assert system_update_history.find_active()["id"] == record["id"]
    system_update_history.update(record["id"], status="succeeded", output="done", completed=True)
    late = system_update_history.update(record["id"], status="running", output="partial")
    assert late["status"] == "succeeded"
    assert late["output"] == "done"
    assert system_update_history.find_active() is None


def test_long_output_keeps_the_end_of_the_log():
    output = system_update_history.sanitise_output("x" * 40_000 + "FINAL LINE")
    assert output.endswith("FINAL LINE")
    assert len(output) == system_update_history._MAX_OUTPUT


def test_report_helper_reads_output_from_stdin(history, tmp_path):
    record = system_update_history.create_pending(
        requested_at="2026-09-29T00:00:00+00:00", target_revision="v1", source="web",
    )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/system_update_report.py"), record["id"], "running",
         "--output-file", "-"],
        input="Pulling image…\n", text=True, check=True,
        env={**os.environ, "PYTHONPATH": str(ROOT), "MYPORTAL_SYSTEM_UPDATE_HISTORY_DIR": str(history)},
    )
    assert system_update_history.get(record["id"])["output"] == "Pulling image…\n"


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [("v0.10.0", "v0.9.9", True), ("v1.0.0", "v1.0.0", False), ("v0.5.0", "v0.5.1", False), ("0.6.0", "v0.5.1", True)],
)
def test_version_comparison_matches_docker_script(candidate, current, newer):
    assert system_updates._version_newer(candidate, current) is newer


def test_docker_request_queues_one_update_for_the_host(monkeypatch, history):
    _docker(monkeypatch)
    result = asyncio.run(system_updates.request_update())
    assert result["created"] is True
    record = result["record"]
    assert record["status"] == "pending"
    assert record["mode"] == "docker"
    assert record["target_revision"] == "v1.1.0"
    flag = system_updates._FLAG_PATH.read_text()
    assert f"update_id={record['id']}\n" in flag
    assert oct(system_updates._FLAG_PATH.stat().st_mode & 0o777) == "0o600"

    again = asyncio.run(system_updates.request_update())
    assert again["created"] is False
    assert again["record"]["id"] == record["id"]


def test_docker_request_is_refused_when_up_to_date(monkeypatch, history):
    _docker(monkeypatch, installed="v1.1.0", latest="v1.1.0")
    result = asyncio.run(system_updates.request_update())
    assert result["record"] is None
    assert not system_updates._FLAG_PATH.exists()


def test_pending_request_can_be_cancelled(monkeypatch, history):
    _docker(monkeypatch)
    record = asyncio.run(system_updates.request_update())["record"]
    cancelled = system_updates.cancel_request(record["id"])
    assert cancelled["status"] == "failed"
    assert not system_updates._FLAG_PATH.exists()
    with pytest.raises(ValueError):
        system_updates.cancel_request(record["id"])


def test_pending_request_cancel_succeeds_when_flag_already_missing(monkeypatch, history):
    _docker(monkeypatch)
    record = asyncio.run(system_updates.request_update())["record"]
    system_updates._FLAG_PATH.unlink()

    cancelled = system_updates.cancel_request(record["id"])

    assert cancelled["status"] == "failed"
    assert not system_updates._FLAG_PATH.exists()


def test_unclaimed_request_expires_with_setup_hint(monkeypatch, history):
    _docker(monkeypatch)
    record = asyncio.run(system_updates.request_update())["record"]
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    path = history / f"{record['id']}.json"
    data = json.loads(path.read_text())
    data["started_at"] = stale
    path.write_text(json.dumps(data))

    system_updates.expire_unclaimed_requests()
    expired = system_update_history.get(record["id"])
    assert expired["status"] == "failed"
    assert "web-upgrades on" in expired["error"]
    assert not system_updates._FLAG_PATH.exists()


def test_unclaimed_request_expires_even_when_flag_already_missing(monkeypatch, history):
    _docker(monkeypatch)
    record = asyncio.run(system_updates.request_update())["record"]
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    path = history / f"{record['id']}.json"
    data = json.loads(path.read_text())
    data["started_at"] = stale
    path.write_text(json.dumps(data))
    system_updates._FLAG_PATH.unlink()

    system_updates.expire_unclaimed_requests()

    expired = system_update_history.get(record["id"])
    assert expired["status"] == "failed"
    assert "host did not pick up" in expired["error"]


def test_baremetal_request_uses_tracked_rolling_workflow(monkeypatch, history):
    monkeypatch.delenv("MYPORTAL_DEPLOYMENT", raising=False)
    from app.services.scheduler import scheduler_service

    captured = {}

    async def fake_run(*, source=None, **kwargs):
        captured["source"] = source
        system_update_history.create_pending(
            requested_at=datetime.now(timezone.utc).isoformat(), target_revision="abc", source=source,
        )
        return "queued"

    monkeypatch.setattr(scheduler_service, "run_system_update", fake_run)
    result = asyncio.run(system_updates.request_update())
    assert captured == {"source": "web"}
    assert result["created"] is True
    assert result["record"]["source"] == "web"


def test_web_source_skips_hot_reload_and_requests_rolling_mode(monkeypatch, tmp_path, history):
    from app.services import scheduler as scheduler_module
    from app.services.scheduler import SchedulerService

    monkeypatch.delenv("MYPORTAL_DEPLOYMENT", raising=False)
    flag = tmp_path / "flag"
    monkeypatch.setattr(scheduler_module, "_SYSTEM_UPDATE_FLAG_PATH", flag)
    service = SchedulerService()

    async def ref(*args):
        return "local"

    async def remote(*args):
        return "remote"

    async def fetched(*args):
        return "remote"

    async def changed(*args):
        return ["app/features/demo/routes.py"]

    async def hot_reload(**kwargs):
        raise AssertionError("web requests must not hot reload")

    monkeypatch.setattr(service, "_get_git_ref", ref)
    monkeypatch.setattr(service, "_get_remote_main_ref", remote)
    monkeypatch.setattr(service, "_fetch_remote_main_ref", fetched)
    monkeypatch.setattr(service, "_list_changed_files", changed)
    monkeypatch.setattr(service, "_try_feature_pack_hot_reload", hot_reload)

    asyncio.run(service.run_system_update(source="web"))
    assert "requested_mode=rolling" in flag.read_text()
    record = system_update_history.find_active()
    assert record["source"] == "web"
    assert record["mode"] == "rolling"


def test_update_request_api_requires_global_administrator():
    for endpoint in (check_system_update, request_system_update):
        dependencies = [
            parameter.default.dependency
            for parameter in inspect.signature(endpoint).parameters.values()
            if isinstance(parameter.default, Depends)
        ]
        assert require_super_admin in dependencies


def test_update_pages_offer_button_and_live_progress():
    history_page = (ROOT / "app/templates/admin/system_updates.html").read_text()
    detail = (ROOT / "app/templates/admin/system_update_detail.html").read_text()
    assert '"/admin/system-updates/request"' in history_page
    assert '"confirm":' in history_page
    assert "data-system-update=" in detail
    assert "/static/js/system_updates.js" in detail
    assert "/scheduler/system-updates/" in (ROOT / "app/static/js/system_updates.js").read_text()


def _github_transport(monkeypatch, routes):
    import httpx

    real_client = httpx.AsyncClient
    seen = []

    def handler(request):
        seen.append(request.url.path)
        for path, payload in routes.items():
            if request.url.path == path:
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={})

    monkeypatch.setattr(
        system_updates.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    system_updates._changes_cache.update(key=None, at=0.0, value=None)
    return seen


def test_release_notes_are_split_into_pull_requests():
    items, notes = system_updates._parse_release_notes(
        "## What's Changed\r\n"
        "* Fix the header by @brad in https://github.com/o/r/pull/12\r\n"
        "* Bump deps by @dependabot[bot] in https://github.com/o/r/pull/13\r\n"
        "Thanks everyone\r\n\r\n"
        "**Full Changelog**: https://github.com/o/r/compare/v1...v2"
    )
    assert items == [
        {"title": "Fix the header", "author": "brad", "url": "https://github.com/o/r/pull/12", "number": 12},
        {"title": "Bump deps", "author": "dependabot[bot]", "url": "https://github.com/o/r/pull/13", "number": 13},
    ]
    assert notes == "Thanks everyone"


def test_docker_changes_list_releases_between_installed_and_latest(monkeypatch):
    monkeypatch.setenv("MYPORTAL_DEPLOYMENT", "docker")
    release = lambda tag, **extra: {
        "tag_name": tag, "name": tag, "published_at": "2026-10-01T00:00:00Z",
        "html_url": f"https://github.com/o/r/releases/tag/{tag}",
        "body": f"* Change in {tag} by @brad in https://github.com/o/r/pull/1", **extra,
    }
    _github_transport(monkeypatch, {"/repos/bradhawkins85/MyPortal/releases": [
        release("v1.3.0"), release("v1.2.1-rc1", prerelease=True), release("v1.2.0"),
        release("v1.1.0"), release("v1.0.0"),
    ]})
    check = {"deployment": "docker", "installed": "v1.0.0", "latest": "v1.2.0", "available": True}
    changes = asyncio.run(system_updates.list_changes(check))
    assert changes["error"] is None
    assert [item["tag"] for item in changes["releases"]] == ["v1.2.0", "v1.1.0"]
    assert changes["releases"][0]["changes"][0]["title"] == "Change in v1.2.0"
    assert changes["change_count"] == 2
    assert changes["compare_url"].endswith("/compare/v1.0.0...v1.2.0")


def test_baremetal_changes_list_commits_newest_first(monkeypatch):
    monkeypatch.delenv("MYPORTAL_DEPLOYMENT", raising=False)
    installed, latest = "a" * 40, "b" * 40
    commit = lambda sha, message: {
        "sha": sha, "html_url": f"https://github.com/o/r/commit/{sha}",
        "author": {"login": "brad"},
        "commit": {"message": message, "author": {"name": "Brad", "date": "2026-10-01T00:00:00Z"}},
    }
    _github_transport(monkeypatch, {f"/repos/bradhawkins85/MyPortal/compare/{installed}...{latest}": {
        "html_url": "https://github.com/o/r/compare/x...y", "total_commits": 3,
        "commits": [
            commit("1" * 40, "Fix the header bar"),
            commit("2" * 40, "Merge branch 'main' into feature"),
            commit("3" * 40, "Merge pull request #42 from o/feature\n\nRedesign system updates"),
        ],
    }})
    check = {"deployment": "baremetal", "installed": installed, "latest": latest, "available": True}
    changes = asyncio.run(system_updates.list_changes(check))
    assert changes["error"] is None
    assert [(c["title"], c["number"]) for c in changes["commits"]] == [
        ("Redesign system updates", 42), ("Fix the header bar", None),
    ]
    assert changes["total"] == 3
    assert changes["truncated"] is False


def test_changes_are_not_fetched_when_up_to_date_and_failures_are_reported(monkeypatch):
    seen = _github_transport(monkeypatch, {})
    up_to_date = {"deployment": "docker", "installed": "v1", "latest": "v1", "available": False}
    assert asyncio.run(system_updates.list_changes(up_to_date))["releases"] == []
    assert seen == []
    failing = {"deployment": "docker", "installed": "v1", "latest": "v2", "available": True}
    result = asyncio.run(system_updates.list_changes(failing))
    assert result["error"].startswith("Could not list the changes")


def _release(tag):
    return {
        "tag_name": tag, "name": tag, "published_at": "2026-10-01T00:00:00Z",
        "html_url": f"https://github.com/o/r/releases/tag/{tag}",
        "body": f"* Change in {tag} by @brad in https://github.com/o/r/pull/1",
    }


def test_docker_request_records_the_starting_version(monkeypatch, history):
    _docker(monkeypatch, installed="v1.0.0", latest="v1.1.0")
    record = asyncio.run(system_updates.request_update())["record"]
    assert system_update_history.get(record["id"])["from_revision"] == "v1.0.0"


def test_finished_update_stores_its_changes(monkeypatch, history):
    monkeypatch.setenv("MYPORTAL_DEPLOYMENT", "docker")
    seen = _github_transport(monkeypatch, {"/repos/bradhawkins85/MyPortal/releases": [
        _release("v1.2.0"), _release("v1.1.0"), _release("v1.0.0"),
    ]})
    record = system_update_history.create_pending(
        requested_at="2026-09-29T00:00:00+00:00", target_revision="v1.2.0",
        source="web", mode="docker", from_revision="v1.0.0",
    )
    running = asyncio.run(system_updates.changes_for_update(record))
    assert [item["tag"] for item in running["releases"]] == ["v1.2.0", "v1.1.0"]
    assert "changes" not in system_update_history.get(record["id"])  # never races the host job

    finished = system_update_history.update(record["id"], status="succeeded", completed=True)
    asyncio.run(system_updates.changes_for_update(finished))
    stored = system_update_history.get(record["id"])["changes"]
    assert stored["change_count"] == 2
    assert stored["from_revision"] == "v1.0.0"
    assert stored["from_inferred"] is False

    calls = len(seen)
    assert asyncio.run(system_updates.changes_for_update(system_update_history.get(record["id"]))) == stored
    assert len(seen) == calls


def test_backfill_infers_the_start_from_the_previous_successful_update(monkeypatch, history):
    monkeypatch.setenv("MYPORTAL_DEPLOYMENT", "docker")
    _github_transport(monkeypatch, {"/repos/bradhawkins85/MyPortal/releases": [
        _release("v1.3.0"), _release("v1.2.0"), _release("v1.1.0"), _release("v1.0.0"),
    ]})

    def old_update(day, target, status):
        record = system_update_history.create_pending(
            requested_at=f"2026-09-{day:02d}T00:00:00+00:00", target_revision=target,
            source="web", mode="docker",
        )
        return system_update_history.update(record["id"], status=status, completed=True)

    first = old_update(1, "v1.1.0", "succeeded")
    failed = old_update(2, "v1.2.0", "failed")
    latest = old_update(3, "v1.3.0", "succeeded")
    asyncio.run(system_updates.backfill_changes(system_update_history.list_updates()))

    first_changes = system_update_history.get(first["id"])["changes"]
    assert [item["tag"] for item in first_changes["releases"]] == ["v1.1.0"]  # no earlier start known
    assert first_changes["from_revision"] == ""
    failed_changes = system_update_history.get(failed["id"])["changes"]
    assert [item["tag"] for item in failed_changes["releases"]] == ["v1.2.0"]
    latest_changes = system_update_history.get(latest["id"])["changes"]
    assert latest_changes["from_revision"] == "v1.1.0"  # skips the failed update
    assert latest_changes["from_inferred"] is True
    assert [item["tag"] for item in latest_changes["releases"]] == ["v1.3.0", "v1.2.0"]


def test_backfill_covers_the_whole_recorded_history(monkeypatch, history):
    """The default backfill no longer stops after five old updates."""
    monkeypatch.setenv("MYPORTAL_DEPLOYMENT", "docker")
    _github_transport(monkeypatch, {"/repos/bradhawkins85/MyPortal/releases": [
        _release(f"v1.{number}.0") for number in range(1, 9)
    ]})

    def old_update(day, target):
        record = system_update_history.create_pending(
            requested_at=f"2026-09-{day:02d}T00:00:00+00:00", target_revision=target,
            source="web", mode="docker",
        )
        return system_update_history.update(record["id"], status="succeeded", completed=True)

    updates = [old_update(day, f"v1.{day}.0") for day in range(1, 9)]  # 8 > old limit of 5
    asyncio.run(system_updates.backfill_changes(system_update_history.list_updates()))

    for record in updates:
        stored = system_update_history.get(record["id"])
        assert stored["changes"]["releases"] and stored["changes"]["releases"][0]["tag"] == record["target_revision"]


def test_latest_succeeded_update_ignores_statuses_and_other_deployments(history):
    def terminal(day, target, status, mode):
        record = system_update_history.create_pending(
            requested_at=f"2026-09-{day:02d}T00:00:00+00:00", target_revision=target,
            source="web", mode=mode,
        )
        return system_update_history.update(record["id"], status=status, completed=status != "succeeded")

    docker_newest = terminal(12, "v1.3.0", "succeeded", "docker")
    terminal(10, "v1.2.0", "failed", "docker")
    rolling = terminal(5, "a" * 40, "succeeded", "rolling")

    updates = system_update_history.list_updates()
    assert system_updates.latest_succeeded_update(updates, "docker")["id"] == docker_newest["id"]
    assert system_updates.latest_succeeded_update(updates, "baremetal")["id"] == rolling["id"]
    assert system_updates.latest_succeeded_update(updates) is not None
    assert system_updates.latest_succeeded_update([], "docker") is None


def test_list_page_shows_the_last_upgrade_when_up_to_date():
    history_page = (ROOT / "app/templates/admin/system_updates.html").read_text()
    assert "What changed in your last upgrade" in history_page
    assert "last_update_changes" in history_page
    assert 'href="/admin/system-updates/{{ last_update.id }}"' in history_page


def _admin_updates_client(monkeypatch, *, check, fetch):
    """A TestClient on /admin/system-updates for one super-admin with faked checks."""
    from fastapi.testclient import TestClient

    import app.main as main_module
    from app.core.database import db
    from app.main import app

    async def _noop(*args, **kwargs):
        return None

    async def _fake_get_module(slug, *, redact=True):
        return None

    monkeypatch.setattr(db, "connect", _noop)
    monkeypatch.setattr(db, "disconnect", _noop)
    monkeypatch.setattr(db, "run_migrations", _noop)
    monkeypatch.setattr(main_module.change_log_service, "sync_change_log_sources", _noop)
    monkeypatch.setattr(main_module.modules_service, "ensure_default_modules", _noop)
    monkeypatch.setattr(main_module.modules_service, "get_module", _fake_get_module)
    monkeypatch.setattr(main_module.automations_service, "refresh_all_schedules", _noop)
    monkeypatch.setattr(main_module.scheduler_service, "start", _noop)
    monkeypatch.setattr(main_module.scheduler_service, "stop", _noop)
    monkeypatch.setattr(main_module.m365_jobs_service, "start_worker", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module.m365_jobs_service, "stop_worker", _noop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)

    async def fake_require(request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require)
    monkeypatch.setattr(system_updates, "check_for_update", check)
    if fetch is not None:
        monkeypatch.setattr(system_updates, "_fetch_changes", fetch)
    return TestClient(app)


def _release_changes_payload(tag, from_tag):
    return {
        "kind": "releases",
        "releases": [{
            "tag": tag, "name": tag, "published_at": "2026-09-28T00:00:00Z",
            "url": f"https://github.com/o/r/releases/tag/{tag}",
            "changes": [{"title": "Shinier system updates", "author": "brad",
                         "url": "https://github.com/o/r/pull/9", "number": 9}],
            "notes": "",
        }],
        "total": 1, "truncated": False, "change_count": 1,
        "compare_url": f"https://github.com/o/r/compare/{from_tag}...{tag}",
    }


def test_list_page_renders_last_upgrade_changes_when_docker_is_up_to_date(monkeypatch, history):
    # One finished Docker upgrade (v1.1.0 -> v1.2.0) and an up-to-date install.
    record = system_update_history.create_pending(
        requested_at="2026-09-28T00:00:00+00:00", target_revision="v1.2.0",
        source="web", mode="docker", from_revision="v1.1.0",
    )
    system_update_history.update(record["id"], status="succeeded", completed=True)

    async def fake_check(*, refresh=False):
        return {
            "deployment": "docker", "installed": "v1.2.0", "latest": "v1.2.0",
            "available": False, "error": None, "checked_at": "2026-09-29T00:00:00+00:00",
        }

    fetched: list[tuple[str, str, str]] = []

    async def fake_fetch(deployment, installed, latest, *, refresh=False):
        fetched.append((deployment, installed, latest))
        return _release_changes_payload(latest, installed)

    client = _admin_updates_client(monkeypatch, check=fake_check, fetch=fake_fetch)
    with client:
        response = client.get("/admin/system-updates")

    assert response.status_code == 200
    body = response.text
    assert "What changed in your last upgrade" in body
    assert "Shinier system updates" in body
    assert f"/admin/system-updates/{record['id']}" in body  # "See this upgrade"
    assert "What's new since your version" not in body
    assert '<span class="tag tag--success">Up to date</span>' in body
    assert "MyPortal is already up to date" in body  # "Update now" is disabled
    assert fetched == [("docker", "v1.1.0", "v1.2.0")]  # fetched once, then reused
    assert "changes" in system_update_history.get(record["id"])  # stored for the history row


def test_list_page_still_shows_pending_changes_when_upgrade_available(monkeypatch, history):
    async def fake_check(*, refresh=False):
        return {
            "deployment": "docker", "installed": "v1.1.0", "latest": "v1.2.0",
            "available": True, "error": None, "checked_at": "2026-09-29T00:00:00+00:00",
        }

    async def fake_fetch(deployment, installed, latest, *, refresh=False):
        return _release_changes_payload(latest, installed)

    client = _admin_updates_client(monkeypatch, check=fake_check, fetch=fake_fetch)
    with client:
        body = client.get("/admin/system-updates").text

    assert "What's new since your version" in body
    assert "between v1.1.0 and v1.2.0" in body
    assert '<span class="tag tag--warning">Update available</span>' in body
    assert "Shinier system updates" in body
    assert "Compare on GitHub" in body
    assert "What changed in your last upgrade" not in body
    assert "MyPortal is already up to date" not in body  # "Update now" is enabled


def test_backfill_does_not_store_github_failures(monkeypatch, history):
    monkeypatch.setenv("MYPORTAL_DEPLOYMENT", "docker")
    _github_transport(monkeypatch, {})
    record = system_update_history.create_pending(
        requested_at="2026-09-29T00:00:00+00:00", target_revision="v1.2.0",
        source="web", mode="docker", from_revision="v1.0.0",
    )
    system_update_history.update(record["id"], status="succeeded", completed=True)
    asyncio.run(system_updates.backfill_changes(system_update_history.list_updates()))
    assert "changes" not in system_update_history.get(record["id"])


# ---------------------------------------------------------------------------
# Host coordinators
# ---------------------------------------------------------------------------

def _fake_report_helper(path: Path, log: Path) -> None:
    path.write_text(
        "import sys\n"
        f"with open({str(log)!r}, 'a') as h:\n"
        "    args = sys.argv[1:]\n"
        "    out = ''\n"
        "    if '--output-file' in args:\n"
        "        f = args[args.index('--output-file') + 1]\n"
        "        out = sys.stdin.read() if f == '-' else open(f).read()\n"
        "    h.write(' '.join(args[:2]) + '|' + out.replace('\\n', '\\\\n') + '\\n')\n"
    )


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock is required")
def test_baremetal_coordinator_reports_progress_while_upgrading(tmp_path):
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(ROOT / "scripts/process_update_flag.sh", scripts)
    log = tmp_path / "reports.log"
    _fake_report_helper(scripts / "system_update_report.py", log)
    (scripts / "upgrade.sh").write_text("#!/usr/bin/env bash\necho step-one\nsleep 1\necho step-two\n")
    (scripts / "upgrade.sh").chmod(0o755)
    state = project / "var/state"
    state.mkdir(parents=True)
    update_id = "0b5a3c1e-9a0f-4d7e-8a3b-5d2f1c0e9a7b"
    flag = state / "system_update.flag"
    flag.write_text(f"update_id={update_id}\nrequested_mode=rolling\n")
    flag.chmod(0o600)

    subprocess.run(
        ["bash", str(scripts / "process_update_flag.sh")], check=True, capture_output=True, text=True,
        env={
            **os.environ, "MYPORTAL_SHARED_ROOT": str(tmp_path / "none"),
            "MYPORTAL_UPDATER_STATE_DIR": str(tmp_path / "updater"),
            "SYSTEM_UPDATE_PROGRESS_INTERVAL": "0.2",
        },
    )

    # Root-only artifacts (lock, captured output) never touch the
    # service-writable state directory; output reaches the helper on stdin.
    assert sorted(p.name for p in state.iterdir()) == []
    assert oct((tmp_path / "updater").stat().st_mode & 0o777) == "0o700"
    assert not list((tmp_path / "updater").glob("system-update-output.*"))
    reports = log.read_text().splitlines()
    assert reports[0] == f"{update_id} running|"
    assert any(line.startswith(f"{update_id} running|step-one") for line in reports[1:-1])
    assert reports[-1].startswith(f"{update_id} succeeded|step-one")
    assert "step-two" in reports[-1]
    assert not flag.exists()


DOCKER_SCRIPT = ROOT / "scripts" / "myportal-docker.sh"


def _run_process_requests(tmp_path: Path, flag_contents: str, *, upgrade_to: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    home = tmp_path / "docker"
    home.mkdir()
    (home / ".env").write_text("MYPORTAL_VERSION=v1.0.0\n")
    (home / "docker-compose.yml").write_text("services: {}\n")
    flag = tmp_path / "flag"
    flag.write_text(flag_contents)
    reports = tmp_path / "reports.log"
    fake = tmp_path / "myportal-docker"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "echo \"upgrading $*\"\n"
        f"sed -i 's/^MYPORTAL_VERSION=.*/MYPORTAL_VERSION={upgrade_to}/' \"$MYPORTAL_DIR/.env\"\n"
    )
    fake.chmod(0o755)
    snippet = f'''
require_root() {{ :; }}
compose() {{ :; }}
app_exec() {{
  # Simulate "docker exec" into the serving application container.
  local args="$*"
  case "$args" in
    *"sh -c"*) [[ -f "{flag}" ]] && cat "{flag}" ;;
    *"rm -f"*) rm -f "{flag}" ;;
    *system_update_report.py*)
      local rest="${{args#*system_update_report.py }}"
      printf '%s|%s\\n' "${{rest% --output-file -}}" "$(tr '\\n' ' ' )" >> "{reports}" ;;
  esac
}}
INSTALLED_SCRIPT="{fake}"
MYPORTAL_PROGRESS_INTERVAL=0.2
cmd_process_requests
'''
    program = f'source <(sed \'$d\' "{DOCKER_SCRIPT}")\n' + snippet
    result = subprocess.run(
        ["bash", "-c", program], text=True, capture_output=True, check=False,
        env={**os.environ, "MYPORTAL_DIR": str(home)},
    )
    lines = reports.read_text().splitlines() if reports.exists() else []
    return result, lines


UPDATE_ID = "0b5a3c1e-9a0f-4d7e-8a3b-5d2f1c0e9a7b"


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock is required")
def test_docker_host_applies_request_and_reports_result(tmp_path):
    result, reports = _run_process_requests(
        tmp_path, f"update_id={UPDATE_ID}\ntarget_version=v0.0.1-evil\n", upgrade_to="v1.1.0",
    )
    assert result.returncode == 0, result.stderr
    assert "upgrading upgrade --yes" in result.stdout  # never the requested version
    assert reports[0].startswith(f"{UPDATE_ID} running|")
    assert reports[-1].startswith(f"{UPDATE_ID} succeeded|")
    assert "upgrading upgrade --yes" in reports[-1]
    assert not (tmp_path / "flag").exists()


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock is required")
def test_docker_host_reports_skipped_upgrade_as_failure(tmp_path):
    result, reports = _run_process_requests(tmp_path, f"update_id={UPDATE_ID}\n", upgrade_to="v1.0.0")
    assert result.returncode == 1
    assert reports[-1].startswith(f"{UPDATE_ID} failed --error No upgrade was applied")


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock is required")
def test_docker_host_ignores_request_without_valid_id(tmp_path):
    result, reports = _run_process_requests(tmp_path, "update_id=../../etc\n", upgrade_to="v1.1.0")
    assert result.returncode == 0
    assert reports == []
    assert "ignoring an upgrade request" in result.stderr
    assert not (tmp_path / "flag").exists()


def test_docker_script_documents_web_upgrades():
    result = subprocess.run(["bash", str(DOCKER_SCRIPT), "help"], text=True, capture_output=True, check=False)
    assert "web-upgrades on|off|status" in result.stdout
    assert "process-requests" in result.stdout


# ---------------------------------------------------------------------------
# Bare-metal version check without a readable control checkout
# ---------------------------------------------------------------------------

REVISION = "a" * 40


class _FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("boom", request=None, response=None)


def _fake_github(monkeypatch, text, status=200, seen=None):
    import httpx

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            if seen is not None:
                seen.append(url)
            return _FakeResponse(text, status)

    monkeypatch.setattr(httpx, "AsyncClient", Client)


def test_remote_ref_falls_back_to_github_api_when_git_fails(monkeypatch):
    from app.services.scheduler import SchedulerService

    service = SchedulerService()

    async def no_git():
        return None

    monkeypatch.setattr(service, "_ls_remote_main_ref", no_git)
    monkeypatch.setenv("MYPORTAL_REPO", "")  # blank, as shipped in .env.example
    seen = []
    _fake_github(monkeypatch, REVISION + "\n", seen=seen)
    assert asyncio.run(service._get_remote_main_ref()) == REVISION
    assert seen == ["https://api.github.com/repos/bradhawkins85/MyPortal/commits/main"]


def test_remote_ref_rejects_unexpected_github_response(monkeypatch):
    from app.services.scheduler import SchedulerService

    service = SchedulerService()

    async def no_git():
        return None

    monkeypatch.setattr(service, "_ls_remote_main_ref", no_git)
    _fake_github(monkeypatch, "<html>rate limited</html>")
    assert asyncio.run(service._get_remote_main_ref()) is None


def test_ls_remote_handles_unreachable_checkout(monkeypatch, tmp_path):
    from app.services import scheduler as scheduler_module
    from app.services.scheduler import SchedulerService

    monkeypatch.setattr(scheduler_module, "_git_context", lambda: (tmp_path / "missing", []))
    assert asyncio.run(SchedulerService()._ls_remote_main_ref()) is None


def test_baremetal_check_reports_the_reason(monkeypatch):
    from app.services.scheduler import scheduler_service

    monkeypatch.delenv("MYPORTAL_DEPLOYMENT", raising=False)
    system_updates._check_cache.update(at=0.0, value=None)

    async def head(ref):
        return REVISION

    async def remote():
        return None

    monkeypatch.setattr(scheduler_service, "_get_git_ref", head)
    monkeypatch.setattr(scheduler_service, "_get_remote_main_ref", remote)
    result = asyncio.run(system_updates.check_for_update(refresh=True))
    assert "GitHub API" in result["error"]

    async def newer():
        return "b" * 40

    monkeypatch.setattr(scheduler_service, "_get_remote_main_ref", newer)
    result = asyncio.run(system_updates.check_for_update(refresh=True))
    assert result["error"] is None
    assert result["available"] is True


def test_release_revision_falls_back_to_release_directory_name(monkeypatch, tmp_path):
    from app.services import scheduler as scheduler_module

    release = tmp_path / REVISION
    release.mkdir()
    (release / "version.txt").write_text("20260313061816\n")
    monkeypatch.setattr(scheduler_module, "_PROJECT_ROOT", release)
    assert scheduler_module._release_revision() == REVISION


def test_upgrade_installs_stable_command_for_the_control_checkout(tmp_path):
    script = (ROOT / "scripts/upgrade.sh").read_text()
    start = script.index("install_upgrade_command() {")
    end = script.index("\n}\n", start) + 3
    checkout = tmp_path / "my checkout"
    command = tmp_path / "bin" / "myportal-upgrade"
    program = (
        f'PROJECT_ROOT={str(checkout)!r}; SCRIPT_DIR={str(checkout / "scripts")!r}\n'
        + script[start:end]
        + "install_upgrade_command\n"
    )
    subprocess.run(["bash", "-c", program], check=True,
                   env={**os.environ, "MYPORTAL_UPGRADE_COMMAND": str(command)})
    (checkout / "scripts").mkdir(parents=True)
    fake = checkout / "scripts" / "upgrade.sh"
    fake.write_text('#!/usr/bin/env bash\necho "ran $*"\n')
    fake.chmod(0o755)
    result = subprocess.run([str(command), "--rolling"], text=True, capture_output=True, check=True)
    assert result.stdout == "ran --rolling\n"


def test_docs_use_the_stable_upgrade_command():
    for rel in (
        "docs/wiki/getting-started/Zero Downtime Upgrades.md",
        "docs/wiki/getting-started/Setup and Installation.md",
        "docs/wiki/getting-started/Running as a Service.md",
        "wiki/Setup-and-Installation.md",
        "wiki/Systemd-Service.md",
    ):
        text = (ROOT / rel).read_text()
        assert "/opt/myportal/control/scripts/upgrade.sh" not in text, rel
        assert "myportal-upgrade" in text, rel


def _run_record_control_checkout(env_file: Path, checkout: Path) -> subprocess.CompletedProcess[str]:
    script = (ROOT / "scripts/upgrade.sh").read_text()
    start = script.index("record_control_checkout() {")
    end = script.index("\nPY\n}\n", start) + 6
    program = (
        f"ENV_FILE={str(env_file)!r}; PROJECT_ROOT={str(checkout)!r}\n"
        + script[start:end]
        + "record_control_checkout\n"
    )
    return subprocess.run(["bash", "-c", program], text=True, capture_output=True, check=True)


@pytest.mark.parametrize(
    "existing",
    ["", "MYPORTAL_CONTROL_CHECKOUT=\n", "MYPORTAL_CONTROL_CHECKOUT=/nowhere\n"],
)
def test_upgrade_records_missing_or_stale_control_checkout(tmp_path, existing):
    checkout = tmp_path / "opt-myportal"
    (checkout / ".git").mkdir(parents=True)
    env_file = tmp_path / "myportal.env"
    env_file.write_text(f"DB_HOST=localhost\n{existing}")
    env_file.chmod(0o640)
    _run_record_control_checkout(env_file, checkout)
    text = env_file.read_text()
    assert f"MYPORTAL_CONTROL_CHECKOUT={checkout}\n" in text
    assert text.count("MYPORTAL_CONTROL_CHECKOUT=") == 1
    assert text.startswith("DB_HOST=localhost\n")
    assert oct(env_file.stat().st_mode & 0o777) == "0o640"


def test_upgrade_keeps_a_valid_control_checkout(tmp_path):
    other = tmp_path / "elsewhere"
    (other / ".git").mkdir(parents=True)
    env_file = tmp_path / "myportal.env"
    env_file.write_text(f"MYPORTAL_CONTROL_CHECKOUT={other}\n")
    _run_record_control_checkout(env_file, tmp_path / "checkout")
    assert env_file.read_text() == f"MYPORTAL_CONTROL_CHECKOUT={other}\n"


def test_git_context_falls_back_to_default_checkouts(monkeypatch, tmp_path):
    from app.services import scheduler as scheduler_module

    release = tmp_path / "release"
    release.mkdir()
    legacy = tmp_path / "opt-myportal"
    (legacy / ".git").mkdir(parents=True)
    monkeypatch.setattr(scheduler_module, "_PROJECT_ROOT", release)
    monkeypatch.setattr(scheduler_module, "_DEFAULT_CONTROL_CHECKOUTS", (str(legacy),))
    monkeypatch.delenv("MYPORTAL_CONTROL_CHECKOUT", raising=False)
    assert scheduler_module._git_context() == (legacy, ["-c", f"safe.directory={legacy}"])
    monkeypatch.setenv("MYPORTAL_CONTROL_CHECKOUT", str(tmp_path / "missing"))
    assert scheduler_module._git_context()[0] == legacy


def test_env_example_suggests_legacy_control_checkout():
    assert "\nMYPORTAL_CONTROL_CHECKOUT=/opt/myportal\n" in (ROOT / ".env.example").read_text()


def test_system_updates_has_its_own_administration_menu_item():
    base = (ROOT / "app/templates/base.html").read_text()
    admin_block = base[base.index('<li class="menu__heading" role="presentation">Administration</li>'):]
    assert 'href="/admin/system-updates"' in admin_block
    assert ">System Updates</span>" in admin_block
    # Super-admin only: the link sits inside the is_super_admin block.
    super_admin = admin_block[admin_block.index("{% if is_super_admin %}"):]
    assert 'href="/admin/system-updates"' in super_admin
    scheduled = (ROOT / "app/templates/admin/scheduled_tasks.html").read_text()
    assert "/admin/system-updates" not in scheduled

    from app.repositories.sidebar_preferences import build_default_sidebar_preferences

    groups = {group["label"]: group for group in build_default_sidebar_preferences()["groups"]}
    assert "/admin/system-updates" in groups["Administration"]["items"]


# ---------------------------------------------------------------------------
# Superseded updates
# ---------------------------------------------------------------------------


def _running_update(target: str) -> dict:
    record = system_update_history.create_pending(
        requested_at=datetime.now(timezone.utc).isoformat(), target_revision=target, source="web",
    )
    return system_update_history.update(record["id"], status="running")


def test_docker_update_older_than_installed_is_failed_and_unblocks_requests(monkeypatch, history):
    _docker(monkeypatch, installed="v1.1.0", latest="v1.2.0")
    stuck = _running_update("v1.0.0")

    result = asyncio.run(system_updates.request_update())

    failed = system_update_history.get(stuck["id"])
    assert failed["status"] == "failed"
    assert "Superseded" in failed["error"]
    assert result["created"] is True
    assert result["record"]["target_revision"] == "v1.2.0"


def test_update_targeting_installed_version_stays_active(monkeypatch, history):
    # A rolling upgrade serves the new release before the host reports success.
    _docker(monkeypatch, installed="v1.1.0", latest="v1.1.0")
    current = _running_update("v1.1.0")

    asyncio.run(system_updates.fail_superseded_updates())

    assert system_update_history.get(current["id"])["status"] == "running"


def _baremetal(monkeypatch, *, installed: str, ancestor_rc: int):
    from app.services.scheduler import scheduler_service

    monkeypatch.delenv("MYPORTAL_DEPLOYMENT", raising=False)

    async def head(ref):
        return installed

    async def run_git(*args):
        assert args[:2] == ("merge-base", "--is-ancestor")
        return ancestor_rc, "", ""

    monkeypatch.setattr(scheduler_service, "_get_git_ref", head)
    monkeypatch.setattr(scheduler_service, "_run_git", run_git)


def test_baremetal_update_behind_installed_revision_is_failed(monkeypatch, history):
    _baremetal(monkeypatch, installed="b" * 40, ancestor_rc=0)
    stuck = _running_update("a" * 40)

    asyncio.run(system_updates.fail_superseded_updates())

    assert system_update_history.get(stuck["id"])["status"] == "failed"


def test_baremetal_update_ahead_of_installed_revision_stays_active(monkeypatch, history):
    _baremetal(monkeypatch, installed="b" * 40, ancestor_rc=1)
    upgrading = _running_update("a" * 40)

    asyncio.run(system_updates.fail_superseded_updates())

    assert system_update_history.get(upgrading["id"])["status"] == "running"


def test_baremetal_asks_github_when_git_cannot_compare(monkeypatch, history):
    _baremetal(monkeypatch, installed="b" * 40, ancestor_rc=128)
    seen = []
    _fake_github(monkeypatch, "", seen=seen)
    monkeypatch.setattr(_FakeResponse, "json", lambda self: {"status": "ahead"}, raising=False)
    stuck = _running_update("a" * 40)

    asyncio.run(system_updates.fail_superseded_updates())

    assert seen and seen[0].endswith(f"/compare/{'a' * 40}...{'b' * 40}")
    assert system_update_history.get(stuck["id"])["status"] == "failed"
