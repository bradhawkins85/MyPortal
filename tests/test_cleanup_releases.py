import os
from pathlib import Path

import pytest

from scripts.cleanup_releases import cleanup_releases


def _release(root: Path, name: str, timestamp: int) -> Path:
    release = root / name
    release.mkdir()
    os.utime(release, (timestamp, timestamp))
    return release


def test_cleanup_keeps_three_newest_releases(tmp_path: Path) -> None:
    root = tmp_path / "releases"
    root.mkdir()
    releases = [_release(root, f"release-{number}", number) for number in range(1, 6)]
    current = tmp_path / "current"
    current.symlink_to(releases[-1])

    removed = cleanup_releases(root, current, proc_root=None)

    assert {path.name for path in removed} == {"release-1", "release-2"}
    assert {path.name for path in root.iterdir()} == {"release-3", "release-4", "release-5"}


def test_cleanup_preserves_active_release_when_it_is_old(tmp_path: Path) -> None:
    root = tmp_path / "releases"
    root.mkdir()
    releases = [_release(root, f"release-{number}", number) for number in range(1, 6)]
    current = tmp_path / "current"
    current.symlink_to(releases[0])

    cleanup_releases(root, current, proc_root=None)

    assert {path.name for path in root.iterdir()} == {
        "release-1",
        "release-3",
        "release-4",
        "release-5",
    }


def test_cleanup_rejects_active_target_outside_release_root(tmp_path: Path) -> None:
    root = tmp_path / "releases"
    root.mkdir()
    outside = tmp_path / "unrelated"
    outside.mkdir()
    current = tmp_path / "current"
    current.symlink_to(outside)
    _release(root, "release-1", 1)

    with pytest.raises(ValueError, match="outside the release root"):
        cleanup_releases(root, current, proc_root=None)

    assert outside.is_dir()


def test_cleanup_ignores_files_and_symlinked_directories(tmp_path: Path) -> None:
    root = tmp_path / "releases"
    root.mkdir()
    releases = [_release(root, f"release-{number}", number) for number in range(1, 5)]
    current = tmp_path / "current"
    current.symlink_to(releases[-1])
    unrelated = root / "README"
    unrelated.write_text("do not delete", encoding="utf-8")
    linked = root / "linked"
    linked.symlink_to(releases[0])

    cleanup_releases(root, current, proc_root=None)

    assert unrelated.read_text(encoding="utf-8") == "do not delete"
    assert linked.is_symlink()


def test_cleanup_preserves_instance_link_targets(tmp_path: Path) -> None:
    root = tmp_path / "releases"
    root.mkdir()
    releases = [_release(root, f"release-{number}", number) for number in range(1, 6)]
    current = tmp_path / "current"
    current.symlink_to(releases[-1])
    standby = tmp_path / "green"
    standby.symlink_to(releases[0])

    removed = cleanup_releases(
        root, current, protected_links=[standby, tmp_path / "missing"], proc_root=None
    )

    assert {path.name for path in removed} == {"release-2"}


def test_cleanup_preserves_release_used_by_running_process(tmp_path: Path) -> None:
    root = tmp_path / "releases"
    root.mkdir()
    releases = [_release(root, f"release-{number}", number) for number in range(1, 6)]
    current = tmp_path / "current"
    current.symlink_to(releases[-1])
    (releases[0] / "app").mkdir()
    os.utime(releases[0], (1, 1))
    proc = tmp_path / "proc"
    (proc / "123").mkdir(parents=True)
    (proc / "123" / "cwd").symlink_to(releases[0] / "app")
    (proc / "self").mkdir()

    removed = cleanup_releases(root, current, proc_root=proc)

    assert {path.name for path in removed} == {"release-2"}
