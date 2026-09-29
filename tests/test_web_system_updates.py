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
    assert 'action="/admin/system-updates/request"' in history_page
    assert 'partials/csrf.html' in history_page
    assert "data-system-update=" in detail
    assert "/static/js/system_updates.js" in detail
    assert "/scheduler/system-updates/" in (ROOT / "app/static/js/system_updates.js").read_text()


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
        env={**os.environ, "MYPORTAL_SHARED_ROOT": str(tmp_path / "none"), "SYSTEM_UPDATE_PROGRESS_INTERVAL": "0.2"},
    )

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
compose() {{
  # Simulate "docker compose exec/run" against a fake container.
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
