"""Deterministic, fail-closed deployment planning shared by all updaters."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass


PLANNER_PATHS = {"app/services/deployment_plan.py", "scripts/upgrade.sh"}
RUNTIME_SUFFIXES = {".py", ".so", ".sql"}


@dataclass(frozen=True)
class DeploymentPlan:
    schema_version: int
    action: str
    reason: str
    categories: tuple[str, ...]
    changed_paths: tuple[str, ...]
    feature_packs: tuple[str, ...]
    requires_worker_reload: bool

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        for key in ("categories", "changed_paths", "feature_packs"):
            value[key] = list(value[key])
        return value


def _category(path: str) -> tuple[str, str | None]:
    if path in PLANNER_PATHS:
        return "planner", None
    if path.startswith(("docs/", "changes/", "tests/", ".github/")) or path in {
        "README.md", "LICENSE", ".gitignore", ".gitleaks.toml",
    }:
        return "docs", None
    if path.startswith("app/static/"):
        return "static", None
    if path.startswith("app/templates/"):
        return "template", None
    if path.startswith("migrations/") and path.endswith(".sql"):
        return "migration", None
    if path.startswith("app/features/"):
        rest = path.removeprefix("app/features/").split("/", 1)
        return ("feature_pack", rest[0]) if len(rest) == 2 and rest[0] else ("backend", None)
    if path.startswith("tray/"):
        return "tray", None
    if path in {"pyproject.toml", "uv.lock", "requirements.txt", "requirements.lock"}:
        return "dependency", None
    if path.startswith(("deploy/", "scripts/")) or path in {".env.example", "Dockerfile", "docker-compose.yml"}:
        return "configuration", None
    if path.startswith("app/") or path in {"manage.py"}:
        return "backend", None
    return "unknown", None


def build_deployment_plan(entries: list[tuple[str, str]]) -> DeploymentPlan:
    """Build a plan from git ``(status, path)`` entries.

    Deletions, renames/copies and unknown paths deliberately select a cutover.
    Docs are ignored when combined with runtime categories.  The selected
    action is the least disruptive action capable of applying every category.
    """
    categories: set[str] = set()
    packs: set[str] = set()
    paths: list[str] = []
    unsafe = False
    for raw_status, raw_path in entries:
        status, path = raw_status.strip().upper(), raw_path.strip().lstrip("./")
        if not path:
            continue
        paths.append(path)
        category, pack = _category(path)
        categories.add(category)
        if pack:
            packs.add(pack)
        if status.startswith(("D", "R", "C", "T")) and category not in {"docs"}:
            unsafe = True

    effective = categories - {"docs"}
    if not paths or not effective:
        action, reason, reload = "no-op", "documentation_or_tests_only", False
    elif unsafe:
        action, reason, reload = "staged-cutover", "deleted_or_renamed_runtime_file", True
    elif effective & {"planner", "unknown"}:
        action, reason, reload = "staged-cutover", "unknown_or_planner_change", True
    elif effective & {"dependency"}:
        action, reason, reload = "staged-cutover", "dependency_changed", True
    elif effective & {"configuration", "backend"}:
        action, reason, reload = "staged-cutover", "configuration_or_backend_changed", True
    elif "feature_pack" in effective:
        if effective == {"feature_pack"}:
            action, reason, reload = "feature-pack-reload", "feature_pack_only", False
        else:
            action, reason, reload = "staged-cutover", "feature_pack_mixed_with_runtime", True
    elif "migration" in effective and effective <= {"migration", "template", "static"}:
        action, reason, reload = "migration-only", "migration_with_reload_free_assets", False
    elif "template" in effective and effective <= {"template", "static"}:
        action, reason, reload = "template-reload", "templates_with_reload_free_assets", False
    elif effective == {"static"}:
        action, reason, reload = "static-publish", "static_assets_only", False
    elif effective == {"tray"}:
        action, reason, reload = "tray-publish", "tray_only", False
    else:  # pragma: no cover - the branches above intentionally exhaust input
        action, reason, reload = "staged-cutover", "unclassified_change", True
    ordered = tuple(sorted(categories or {"docs"}))
    return DeploymentPlan(1, action, reason, ordered, tuple(sorted(set(paths))), tuple(sorted(packs)), reload)


def plan_git_diff(base: str, target: str) -> DeploymentPlan:
    output = subprocess.run(
        ["git", "diff", "--name-status", "--find-renames", base, target],
        check=True, capture_output=True, text=True,
    ).stdout
    entries: list[tuple[str, str]] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2:
            # For renames, retain the destination for diagnostics; status still
            # forces a fail-closed cutover.
            entries.append((fields[0], fields[-1]))
    return build_deployment_plan(entries)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("target")
    args = parser.parse_args()
    print(json.dumps(plan_git_diff(args.base, args.target).to_dict(), separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
