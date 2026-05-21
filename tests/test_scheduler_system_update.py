from __future__ import annotations
import asyncio
from pathlib import Path

from app.services.scheduler import SchedulerService
from app.repositories import scheduled_tasks as scheduled_tasks_repo


def test_run_now_forces_restart_flag(monkeypatch):
    scheduler = SchedulerService()

    async def fake_get_task(task_id: int):
        return {"id": task_id, "command": "system_update"}

    monkeypatch.setattr(scheduled_tasks_repo, "get_task", fake_get_task)

    async def fake_record_task_run(*args, **kwargs):
        return None

    monkeypatch.setattr(scheduled_tasks_repo, "record_task_run", fake_record_task_run)

    recorded: dict[str, bool] = {}

    async def fake_run_system_update(self, *, force_restart: bool = False):
        recorded["force_restart"] = force_restart
        return "ok"

    monkeypatch.setattr(SchedulerService, "run_system_update", fake_run_system_update)

    asyncio.run(scheduler.run_now(7))

    assert recorded["force_restart"] is True


def test_system_update_schedules_flag_when_remote_ahead(monkeypatch, tmp_path: Path):
    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "system_update.flag"

    async def fake_get_git_ref(self, ref: str):
        assert ref == "HEAD"
        return "localsha"

    async def fake_get_remote_main_ref(self):
        return "remotesha"

    monkeypatch.setattr(SchedulerService, "_get_git_ref", fake_get_git_ref)
    monkeypatch.setattr(SchedulerService, "_get_remote_main_ref", fake_get_remote_main_ref)
    monkeypatch.setattr("app.services.scheduler._SYSTEM_UPDATE_FLAG_PATH", flag_path)

    output = asyncio.run(scheduler._run_system_update(force_restart=True))

    assert "Update scheduled" in output
    assert flag_path.exists()
    contents = flag_path.read_text(encoding="utf-8")
    assert "requested_from_ui=true" in contents
    assert "local_head=localsha" in contents
    assert "remote_head=remotesha" in contents


def test_system_update_skips_when_already_current(monkeypatch, tmp_path: Path):
    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "system_update.flag"

    async def fake_get_git_ref(self, ref: str):
        assert ref == "HEAD"
        return "same"

    async def fake_get_remote_main_ref(self):
        return "same"

    monkeypatch.setattr(SchedulerService, "_get_git_ref", fake_get_git_ref)
    monkeypatch.setattr(SchedulerService, "_get_remote_main_ref", fake_get_remote_main_ref)
    monkeypatch.setattr("app.services.scheduler._SYSTEM_UPDATE_FLAG_PATH", flag_path)

    output = asyncio.run(scheduler._run_system_update())
    assert output == "No GitHub update available; upgrade was not scheduled."
    assert not flag_path.exists()


class _FakeRegistry:
    def __init__(self, packs: dict[str, str]):
        # slug -> version
        self._packs = dict(packs)
        self.reloaded: list[str] = []
        self.reload_should_raise: dict[str, Exception] = {}

    def list(self):
        return [{"slug": slug, "version": version} for slug, version in self._packs.items()]

    async def reload(self, slug: str):
        if slug in self.reload_should_raise:
            raise self.reload_should_raise[slug]
        self.reloaded.append(slug)

        class _State:
            last_error = None

        return _State()


def _install_hot_reload_stubs(
    monkeypatch,
    *,
    registry: _FakeRegistry,
    changed_files: list[str],
    incoming_versions: dict[str, str],
    fetched_head: str = "remotesha",
    merge_rc: int = 0,
):
    """Wire the scheduler git/registry helpers for hot-reload tests."""

    import app.core.features as features_module

    monkeypatch.setattr(features_module, "get_registry", lambda: registry)

    async def fake_fetch(self):
        return fetched_head

    async def fake_diff(self, base, head):
        assert head == fetched_head
        return list(changed_files)

    async def fake_read_version(self, slug, ref):
        assert ref == fetched_head
        return incoming_versions.get(slug)

    merge_calls: list[tuple[str, ...]] = []

    async def fake_run_git(self, *args):
        merge_calls.append(args)
        if args[:2] == ("merge", "--ff-only"):
            return (merge_rc, "", "" if merge_rc == 0 else "merge failed")
        return (0, "", "")

    monkeypatch.setattr(SchedulerService, "_fetch_remote_main_ref", fake_fetch)
    monkeypatch.setattr(SchedulerService, "_list_changed_files", fake_diff)
    monkeypatch.setattr(SchedulerService, "_read_pack_version_at_ref", fake_read_version)
    monkeypatch.setattr(SchedulerService, "_run_git", fake_run_git)

    return merge_calls


def _install_head_stubs(monkeypatch):
    async def fake_get_git_ref(self, ref):
        assert ref == "HEAD"
        return "localsha"

    async def fake_get_remote_main_ref(self):
        return "remotesha"

    monkeypatch.setattr(SchedulerService, "_get_git_ref", fake_get_git_ref)
    monkeypatch.setattr(SchedulerService, "_get_remote_main_ref", fake_get_remote_main_ref)


def test_system_update_hot_reloads_feature_pack_when_version_bumped(
    monkeypatch, tmp_path: Path
):
    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "system_update.flag"
    monkeypatch.setattr("app.services.scheduler._SYSTEM_UPDATE_FLAG_PATH", flag_path)

    _install_head_stubs(monkeypatch)

    registry = _FakeRegistry({"tickets": "1.0.0"})
    merge_calls = _install_hot_reload_stubs(
        monkeypatch,
        registry=registry,
        changed_files=[
            "app/features/tickets/__init__.py",
            "app/features/tickets/portal_routes.py",
        ],
        incoming_versions={"tickets": "1.0.1"},
    )

    output = asyncio.run(scheduler._run_system_update())

    assert output is not None
    assert "Feature pack(s) reloaded without restart" in output
    assert "tickets@1.0.1" in output
    assert registry.reloaded == ["tickets"]
    assert not flag_path.exists()
    assert any(call[:2] == ("merge", "--ff-only") for call in merge_calls)


def test_system_update_hot_reloads_even_when_version_not_bumped(
    monkeypatch, tmp_path: Path
):
    """Pack-only diffs hot-reload even if ``PACK.version`` was not bumped.

    The version literal is diagnostic only — the file diff itself is
    proof the code changed, so a forgotten version bump must not
    silently fall back to a full restart.
    """

    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "system_update.flag"
    monkeypatch.setattr("app.services.scheduler._SYSTEM_UPDATE_FLAG_PATH", flag_path)

    _install_head_stubs(monkeypatch)

    registry = _FakeRegistry({"tickets": "1.0.0"})
    merge_calls = _install_hot_reload_stubs(
        monkeypatch,
        registry=registry,
        changed_files=["app/features/tickets/portal_routes.py"],
        incoming_versions={"tickets": "1.0.0"},  # unchanged
    )

    output = asyncio.run(scheduler._run_system_update())

    assert output is not None
    assert "Feature pack(s) reloaded without restart" in output
    assert "tickets@1.0.0" in output
    assert registry.reloaded == ["tickets"]
    assert not flag_path.exists()
    assert any(call[:2] == ("merge", "--ff-only") for call in merge_calls)


def test_system_update_falls_back_to_restart_when_changes_outside_packs(
    monkeypatch, tmp_path: Path
):
    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "system_update.flag"
    monkeypatch.setattr("app.services.scheduler._SYSTEM_UPDATE_FLAG_PATH", flag_path)

    _install_head_stubs(monkeypatch)

    registry = _FakeRegistry({"tickets": "1.0.0"})
    merge_calls = _install_hot_reload_stubs(
        monkeypatch,
        registry=registry,
        changed_files=[
            "app/features/tickets/portal_routes.py",
            "app/main.py",  # outside feature-pack scope
        ],
        incoming_versions={"tickets": "1.0.1"},
    )

    output = asyncio.run(scheduler._run_system_update())

    assert "Update scheduled" in output
    assert flag_path.exists()
    assert registry.reloaded == []
    assert not any(call[:2] == ("merge", "--ff-only") for call in merge_calls)


def test_system_update_falls_back_when_force_restart_requested(
    monkeypatch, tmp_path: Path
):
    """``force_restart=True`` must bypass the hot-reload optimisation."""

    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "system_update.flag"
    monkeypatch.setattr("app.services.scheduler._SYSTEM_UPDATE_FLAG_PATH", flag_path)

    _install_head_stubs(monkeypatch)

    registry = _FakeRegistry({"tickets": "1.0.0"})

    async def _should_not_run(self, *, local_head, remote_head):
        raise AssertionError("hot reload must be skipped when force_restart=True")

    monkeypatch.setattr(
        SchedulerService, "_try_feature_pack_hot_reload", _should_not_run
    )

    output = asyncio.run(scheduler._run_system_update(force_restart=True))

    assert "Update scheduled" in output
    assert flag_path.exists()
    assert registry.reloaded == []


def test_system_update_falls_back_when_pack_not_loaded(monkeypatch, tmp_path: Path):
    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "system_update.flag"
    monkeypatch.setattr("app.services.scheduler._SYSTEM_UPDATE_FLAG_PATH", flag_path)

    _install_head_stubs(monkeypatch)

    # Touched slug is not currently loaded by the registry → cannot
    # reload, must restart so the host can pick it up.
    registry = _FakeRegistry({"tickets": "1.0.0"})
    merge_calls = _install_hot_reload_stubs(
        monkeypatch,
        registry=registry,
        changed_files=["app/features/brand_new/__init__.py"],
        incoming_versions={"brand_new": "1.0.0"},
    )

    output = asyncio.run(scheduler._run_system_update())

    assert "Update scheduled" in output
    assert flag_path.exists()
    assert registry.reloaded == []
    assert not any(call[:2] == ("merge", "--ff-only") for call in merge_calls)


def test_system_update_falls_back_when_reload_raises(monkeypatch, tmp_path: Path):
    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "system_update.flag"
    monkeypatch.setattr("app.services.scheduler._SYSTEM_UPDATE_FLAG_PATH", flag_path)

    _install_head_stubs(monkeypatch)

    registry = _FakeRegistry({"tickets": "1.0.0"})
    registry.reload_should_raise["tickets"] = RuntimeError("boom")
    _install_hot_reload_stubs(
        monkeypatch,
        registry=registry,
        changed_files=["app/features/tickets/portal_routes.py"],
        incoming_versions={"tickets": "1.0.1"},
    )

    output = asyncio.run(scheduler._run_system_update())

    assert "Update scheduled" in output
    assert flag_path.exists()


def test_classify_feature_pack_changes():
    cls = SchedulerService._classify_feature_pack_changes

    assert cls(["app/features/tickets/portal_routes.py"]) == {"tickets"}
    assert cls(
        [
            "app/features/tickets/portal_routes.py",
            "app/features/service_status/routes.py",
        ]
    ) == {"tickets", "service_status"}
    # A change directly under app/features/ (e.g. the package init) is
    # not a per-pack change.
    assert cls(["app/features/__init__.py"]) is None
    # A change outside the feature-packs directory blocks hot reload.
    assert cls(["app/main.py"]) is None
    assert cls(["app/features/tickets/portal_routes.py", "README.md"]) is None
    assert cls([]) is None


def test_consume_feature_pack_reload_flag_reloads_and_deletes(monkeypatch, tmp_path: Path):
    """Listed slugs are reloaded in-process and the flag file is removed."""

    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "feature_pack_reload.flag"
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text("tickets\nservice_status\n", encoding="utf-8")
    monkeypatch.setattr(
        "app.services.scheduler._FEATURE_PACK_RELOAD_FLAG_PATH", flag_path
    )

    import app.core.features as features_module

    registry = _FakeRegistry({"tickets": "1.0.0", "service_status": "1.0.0"})
    monkeypatch.setattr(features_module, "get_registry", lambda: registry)

    asyncio.run(scheduler._consume_feature_pack_reload_flag())

    assert registry.reloaded == ["tickets", "service_status"]
    assert not flag_path.exists()


def test_consume_feature_pack_reload_flag_noop_when_missing(monkeypatch, tmp_path: Path):
    """Missing flag file is a silent no-op (the common case)."""

    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "feature_pack_reload.flag"
    monkeypatch.setattr(
        "app.services.scheduler._FEATURE_PACK_RELOAD_FLAG_PATH", flag_path
    )

    import app.core.features as features_module

    def _should_not_call():  # pragma: no cover - sanity guard
        raise AssertionError("registry must not be consulted when no flag")

    monkeypatch.setattr(features_module, "get_registry", _should_not_call)

    # Must not raise.
    asyncio.run(scheduler._consume_feature_pack_reload_flag())
    assert not flag_path.exists()


def test_consume_feature_pack_reload_flag_keeps_failed_slugs(monkeypatch, tmp_path: Path):
    """When some slugs fail to reload the flag retains them for retry."""

    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "feature_pack_reload.flag"
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text("tickets\nbroken\n", encoding="utf-8")
    monkeypatch.setattr(
        "app.services.scheduler._FEATURE_PACK_RELOAD_FLAG_PATH", flag_path
    )

    import app.core.features as features_module

    registry = _FakeRegistry({"tickets": "1.0.0", "broken": "1.0.0"})
    registry.reload_should_raise["broken"] = RuntimeError("import boom")
    monkeypatch.setattr(features_module, "get_registry", lambda: registry)

    asyncio.run(scheduler._consume_feature_pack_reload_flag())

    assert registry.reloaded == ["tickets"]
    assert flag_path.exists()
    remaining = [line.strip() for line in flag_path.read_text().splitlines() if line.strip()]
    assert remaining == ["broken"]


def test_consume_feature_pack_reload_flag_skips_unloaded_pack(monkeypatch, tmp_path: Path):
    """Slugs not currently loaded are recorded as failed (keep retrying)."""

    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "feature_pack_reload.flag"
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text("brand_new\n", encoding="utf-8")
    monkeypatch.setattr(
        "app.services.scheduler._FEATURE_PACK_RELOAD_FLAG_PATH", flag_path
    )

    import app.core.features as features_module

    registry = _FakeRegistry({"tickets": "1.0.0"})
    monkeypatch.setattr(features_module, "get_registry", lambda: registry)

    asyncio.run(scheduler._consume_feature_pack_reload_flag())

    assert registry.reloaded == []
    assert flag_path.exists()
    remaining = [line.strip() for line in flag_path.read_text().splitlines() if line.strip()]
    assert remaining == ["brand_new"]
