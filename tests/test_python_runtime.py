"""Bare-metal hosts are moved to the release Python version automatically."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/lib/python_runtime.sh"
UPGRADE = (ROOT / "scripts/upgrade.sh").read_text()
INSTALLER = (ROOT / "scripts/install_environment.sh").read_text()
CURRENT = "%d.%d" % sys.version_info[:2]


def _bin_dir(tmp_path: Path, **links: str) -> Path:
    """A PATH holding only the tools the helper needs plus the given links."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "env", "cat", "mkdir", "ln", "chmod", "rm", "awk", "grep", "id"):
        found = shutil.which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)
    for name, target in links.items():
        (bin_dir / name).symlink_to(target)
    return bin_dir


def _run(tmp_path: Path, script: str, bin_dir: Path, **env: str):
    return subprocess.run(
        ["bash", "-c", f"source {HELPER}\n{script}"],
        text=True,
        capture_output=True,
        env={"PATH": str(bin_dir), "HOME": str(tmp_path), **env},
        check=False,
    )


def test_interpreter_on_path_is_used(tmp_path):
    bin_dir = _bin_dir(tmp_path, **{f"python{CURRENT}": sys.executable})
    result = _run(tmp_path, 'ensure_runtime_python "$ROOT_DIR"', bin_dir,
                  ROOT_DIR=str(tmp_path / "managed"), MYPORTAL_PYTHON_VERSION=CURRENT)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(bin_dir / f"python{CURRENT}")
    assert not (tmp_path / "managed").exists()  # nothing installed


def test_previously_installed_managed_interpreter_is_reused(tmp_path):
    managed = tmp_path / "managed"
    (managed / "bin").mkdir(parents=True)
    (managed / "bin" / f"python{CURRENT}").symlink_to(sys.executable)
    result = _run(tmp_path, 'ensure_runtime_python "$ROOT_DIR"', _bin_dir(tmp_path),
                  ROOT_DIR=str(managed), MYPORTAL_PYTHON_VERSION=CURRENT)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(managed / "bin" / f"python{CURRENT}")


def test_wrong_version_is_not_accepted(tmp_path):
    bin_dir = _bin_dir(tmp_path, **{"python9.99": sys.executable})
    result = _run(tmp_path, 'python_runtime_usable "$CANDIDATE"', bin_dir,
                  CANDIDATE=str(bin_dir / "python9.99"), MYPORTAL_PYTHON_VERSION="9.99")
    assert result.returncode == 1


def test_missing_interpreter_falls_back_to_standalone_install(tmp_path):
    managed = tmp_path / "managed"
    script = f"""
install_runtime_python_package() {{ return 1; }}
install_runtime_python_standalone() {{
  mkdir -p "$1/bin" && ln -s {sys.executable} "$1/bin/python$MYPORTAL_PYTHON_VERSION"
}}
ensure_runtime_python "$ROOT_DIR"
"""
    result = _run(tmp_path, script, _bin_dir(tmp_path),
                  ROOT_DIR=str(managed), MYPORTAL_PYTHON_VERSION=CURRENT)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(managed / "bin" / f"python{CURRENT}")


def test_failed_install_stops_with_actionable_error(tmp_path):
    script = """
install_runtime_python_package() { return 1; }
install_runtime_python_standalone() { return 1; }
ensure_runtime_python "$ROOT_DIR"
"""
    result = _run(tmp_path, script, _bin_dir(tmp_path),
                  ROOT_DIR=str(tmp_path / "managed"), MYPORTAL_PYTHON_VERSION="9.99")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "Install python9.99 and python9.99-venv" in result.stderr


def test_standalone_install_never_writes_outside_managed_root():
    helper = HELPER.read_text()
    assert "python install --no-bin" in helper
    assert 'UV_PYTHON_INSTALL_DIR="${root}/interpreters"' in helper
    assert '"uv==${MYPORTAL_UV_VERSION}"' in helper
    assert 'chmod -R u+rwX,go+rX "$root"' in helper


def test_upgrade_builds_releases_on_the_runtime_python():
    assert 'source "${SCRIPT_DIR}/lib/python_runtime.sh"' in UPGRADE
    resolve = UPGRADE.index('RUNTIME_PYTHON=$(ensure_runtime_python "${SHARED_ROOT}/python")')
    # After the lock and the re-exec into the updated script, before any release is built.
    assert resolve > UPGRADE.index("MYPORTAL_UPGRADE_REEXEC=1 exec bash")
    assert resolve < UPGRADE.index('prepare_release "$TARGET_REVISION" "$RELEASE_DIR"')
    install = UPGRADE[UPGRADE.index("install_dependencies() {"):UPGRADE.index("\nvalidate_tray_artifacts()")]
    assert '"${RUNTIME_PYTHON:-python3}" -m venv "$staging"' in install
    # The interpreter version keys the dependency layer, so a host moved to the
    # new Python gets a fresh virtual environment rather than the cached one.
    assert '"${RUNTIME_PYTHON:-python3}" -c \'import sys; print(sys.implementation.name, *sys.version_info[:2])\'' in install


def test_dependency_layer_is_rebuilt_when_the_runtime_python_changes(tmp_path):
    functions = UPGRADE[UPGRADE.index("record_step() {"):UPGRADE.index("\ninstall_blue_green_service_unit()")]
    release_one, release_two = tmp_path / "one", tmp_path / "two"
    for release in (release_one, release_two):
        release.mkdir()
        (release / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n")
        (release / "requirements.lock").write_text("# exact fixture lock\n")
    fake = tmp_path / "fake"
    fake.mkdir()
    for version in ("3.10", "3.12"):
        stub = fake / f"python{version}"
        stub.write_text(
            "#!/bin/bash\n"
            'if [[ "$1" == -m && "$2" == venv ]]; then mkdir -p "$3/bin"; '
            "printf '#!/bin/sh\\nexit 0\\n' >\"$3/bin/python\"; chmod +x \"$3/bin/python\"; exit 0; fi\n"
            f"echo cpython {version.replace('.', ' ')}\n"
        )
        stub.chmod(0o755)
    command = f"""
set -Eeuo pipefail
SHARED_ROOT={tmp_path / 'shared'}
STEP_REPORT=''
{functions}
RUNTIME_PYTHON={fake / 'python3.10'} install_dependencies {release_one}
RUNTIME_PYTHON={fake / 'python3.12'} install_dependencies {release_two}
printf '%s\\n' "$STEP_REPORT"
readlink {release_one}/.venv
readlink {release_two}/.venv
"""
    result = subprocess.run(["bash", "-c", command], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    report, layer_one, layer_two = result.stdout.strip().splitlines()
    assert [entry.split(":")[1] for entry in report.split(";")] == ["miss", "miss"]
    assert layer_one != layer_two


def test_development_venv_is_rebuilt_on_an_older_python():
    assert 'source "${SCRIPT_DIR}/lib/python_runtime.sh"' in INSTALLER
    ensure = INSTALLER[INSTALLER.index("ensure_virtualenv() {"):INSTALLER.index("\nvenv_python() {")]
    assert 'runtime_python=$(ensure_runtime_python "${PROJECT_ROOT}/.python")' in ensure
    assert '== "$MYPORTAL_PYTHON_VERSION"' in ensure
    assert 'as_checkout_owner "$runtime_python" -m venv "$VENV_DIR"' in ensure


@pytest.mark.parametrize("path", ["pyproject.toml", "requirements.lock"])
def test_release_requires_the_patched_pillow(path):
    text = (ROOT / path).read_text()
    if path == "pyproject.toml":
        assert 'requires-python = ">=3.11"' in text
        assert '"Pillow>=13.0.0"' in text
    else:
        assert "pillow==13.0.0" in text
