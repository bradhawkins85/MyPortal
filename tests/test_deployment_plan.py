import json
import subprocess
import sys

import pytest

from app.services.deployment_plan import build_deployment_plan
from app.services.scheduler import SchedulerService


@pytest.mark.parametrize(
    ("entries", "action", "reload"),
    [
        ([("M", "README.md"), ("A", "tests/test_widget.py")], "no-op", False),
        ([("M", "tray/README.md")], "no-op", False),
        ([("M", "tray/.github/workflows/tray-build.yml")], "no-op", False),
        ([("M", "changes/a.json")], "no-op", False),
        ([("M", "app/static/css/main.css")], "static-publish", False),
        ([("M", "app/templates/base.html")], "template-reload", False),
        ([("A", "migrations/999_expand.sql")], "migration-only", False),
        ([("M", "app/features/tickets/routes.py")], "feature-pack-reload", False),
        ([("M", "tray/main.go")], "tray-publish", False),
        ([("M", "app/main.py")], "staged-cutover", True),
        ([("M", "app/middleware/security.py")], "staged-cutover", True),
        ([("M", "pyproject.toml")], "staged-cutover", True),
        ([("M", "deploy/systemd/myportal@.service")], "staged-cutover", True),
        ([("M", "app/templates/base.html"), ("M", "app/static/main.css")], "template-reload", False),
        ([("A", "migrations/999.sql"), ("M", "app/templates/base.html")], "migration-only", False),
        ([("M", "README.md"), ("M", "app/main.py")], "staged-cutover", True),
        ([("M", "tray/main.go"), ("M", "app/static/main.css")], "staged-cutover", True),
        ([("D", "app/static/removed.js")], "staged-cutover", True),
        ([("R100", "app/templates/new.html")], "staged-cutover", True),
        ([("M", "unexpected/runtime.bin")], "staged-cutover", True),
        ([("M", "app/services/deployment_plan.py")], "staged-cutover", True),
    ],
)
def test_deployment_plan_matrix(entries, action, reload):
    plan = build_deployment_plan(entries)
    assert plan.action == action
    assert plan.requires_worker_reload is reload
    assert plan.reason


def test_scheduler_uses_the_shared_planner():
    paths = ["app/templates/base.html", "app/static/css/main.css"]
    expected = build_deployment_plan([("M", path) for path in paths])
    assert SchedulerService._classify_full_upgrade_reason(paths) == expected.reason


@pytest.mark.parametrize(
    ("entries", "dependencies", "tray", "dependency_reason", "tray_reason"),
    [
        ([('M', 'app/templates/base.html')], False, False, 'dependency_inputs_unchanged', 'tray_inputs_unchanged'),
        ([('A', 'migrations/999.sql')], False, False, 'dependency_inputs_unchanged', 'tray_inputs_unchanged'),
        ([('M', 'app/main.py')], False, False, 'dependency_inputs_unchanged', 'tray_inputs_unchanged'),
        ([('M', 'requirements.lock')], True, False, 'dependency_inputs_changed', 'tray_inputs_unchanged'),
        ([('M', 'tray/README.md')], False, False, 'dependency_inputs_unchanged', 'tray_inputs_unchanged'),
        ([('M', 'tray/.github/workflows/tray-build.yml')], False, False, 'dependency_inputs_unchanged', 'tray_inputs_unchanged'),
        ([('M', 'tray/go.mod')], False, True, 'dependency_inputs_unchanged', 'tray_inputs_changed'),
        ([('M', 'app/main.py'), ('M', 'tray/go.sum')], False, True, 'dependency_inputs_unchanged', 'tray_inputs_changed'),
    ],
)
def test_plan_drives_dependency_and_artifact_work(entries, dependencies, tray, dependency_reason, tray_reason):
    plan = build_deployment_plan(entries)
    assert plan.install_dependencies is dependencies
    assert plan.validate_tray_artifacts is tray
    assert plan.dependency_reason == dependency_reason
    assert plan.tray_reason == tray_reason


def test_planner_cli_emits_the_same_plan(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("old\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "old"], cwd=tmp_path, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    (tmp_path / "README.md").write_text("new\n")
    subprocess.run(["git", "commit", "-qam", "new"], cwd=tmp_path, check=True)
    target = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    output = subprocess.check_output(
        [sys.executable, "-m", "app.services.deployment_plan", base, target],
        cwd=tmp_path,
        text=True,
        env={"PYTHONPATH": str(__import__("pathlib").Path(__file__).parents[1])},
    )
    assert json.loads(output) == build_deployment_plan([("M", "README.md")]).to_dict()
