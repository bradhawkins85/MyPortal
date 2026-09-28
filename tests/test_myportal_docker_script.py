"""Unit tests for the helpers in scripts/myportal-docker.sh.

The end-to-end install/upgrade/rollback flow needs a Docker host and is covered
by .github/workflows/docker-release.yml; these tests exercise the pure shell
logic that decides what to install.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "myportal-docker.sh"


def _run(snippet: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # Load every function without running main (the script's last line).
    program = f'source <(sed \'$d\' "{SCRIPT}")\n{snippet}'
    return subprocess.run(
        ["bash", "-c", program],
        text=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


def test_script_is_valid_bash_and_ends_with_main():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    assert SCRIPT.read_text(encoding="utf-8").rstrip().splitlines()[-1] == 'main "$@"'


def test_help_lists_commands():
    result = subprocess.run(["bash", str(SCRIPT), "help"], text=True, capture_output=True, check=False)
    assert result.returncode == 0
    for command in ("install", "upgrade", "check", "backup", "restore-db", "auto-upgrade", "superadmin", "user verify"):
        assert command in result.stdout


def test_unknown_command_fails():
    result = subprocess.run(["bash", str(SCRIPT), "bogus"], text=True, capture_output=True, check=False)
    assert result.returncode == 2


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        ("v0.6.0", "v0.5.1", True),
        ("v0.10.0", "v0.9.9", True),
        ("v1.0.0", "v1.0.0", False),
        ("v0.5.0", "v0.5.1", False),
        ("0.6.0", "v0.5.1", True),
    ],
)
def test_version_newer(candidate, current, newer):
    result = _run(f'version_newer "{candidate}" "{current}"')
    assert (result.returncode == 0) is newer


@pytest.mark.parametrize(
    ("tag", "valid"),
    [("v0.6.0", True), ("v1.2.3-rc.1", True), ("", False), ("../x", False), ("v1;rm", False)],
)
def test_valid_tag(tag, valid):
    assert (_run(f'valid_tag "{tag}"').returncode == 0) is valid


def test_set_and_get_setting_round_trip(tmp_path):
    config = tmp_path / "settings.env"
    config.write_text("# comment\nKEEP=a=b\nMYPORTAL_VERSION=v1\n", encoding="utf-8")
    config.chmod(0o600)

    result = _run(
        f'set_setting "{config}" MYPORTAL_VERSION v2\n'
        f'set_setting "{config}" MYPORTAL_IMAGE ghcr.io/owner/myportal:v2\n'
        f'printf "[%s]\\n" "$(get_setting "{config}" MYPORTAL_VERSION)"\n'
        f'printf "[%s]\\n" "$(get_setting "{config}" KEEP)"\n'
        f'printf "[%s]\\n" "$(get_setting "{config}" MISSING)"'
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["[v2]", "[a=b]", "[]"]
    assert config.read_text(encoding="utf-8") == (
        "# comment\nKEEP=a=b\nMYPORTAL_VERSION=v2\nMYPORTAL_IMAGE=ghcr.io/owner/myportal:v2\n"
    )
    assert config.stat().st_mode & 0o777 == 0o600


def test_latest_release_tag_parses_github_response(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> "$CALLS"\n'
        "printf '{\\n  \"url\": \"x\",\\n  \"tag_name\": \"v0.6.0\",\\n  \"name\": \"Release\"\\n}'\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    calls = tmp_path / "calls"

    result = _run(
        "latest_release_tag",
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls)},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "v0.6.0"
    assert "https://api.github.com/repos/bradhawkins85/MyPortal/releases/latest" in calls.read_text()


def test_random_secret_length_and_alphabet():
    result = _run("random_secret 64")
    assert result.returncode == 0
    assert len(result.stdout) == 64
    assert set(result.stdout) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")


def test_image_repository_defaults_to_lowercase_ghcr_path():
    result = _run('printf "%s" "$MYPORTAL_IMAGE_REPO"')
    assert result.stdout == "ghcr.io/bradhawkins85/myportal"


def test_sql_string_hex_encodes_the_value():
    # 'x' OR 1=1 -- must reach MariaDB as data, never as SQL.
    result = _run("sql_string \"x' OR 1=1 --\"")
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "CONVERT(UNHEX('7827204f5220313d31202d2d') USING utf8mb4) COLLATE utf8mb4_unicode_ci"
    )


@pytest.mark.parametrize(
    "args",
    [
        "",
        "bogus",
        "grant",
        "grant a@example.com b@example.com",
        "revoke a@example.com --bogus",
        "list a@example.com",
        "create",
        "create a@example.com --first-name",
        "reset-password",
    ],
)
def test_superadmin_rejects_bad_usage_before_touching_docker(args):
    result = subprocess.run(
        ["bash", "-c", f'"{SCRIPT}" superadmin {args}'],
        text=True,
        capture_output=True,
        env={**os.environ, "MYPORTAL_DIR": "/nonexistent"},
        check=False,
    )
    assert result.returncode != 0
    assert "Error:" in result.stderr


@pytest.mark.parametrize(
    ("stdin", "ok"),
    [("a-long-enough-password\n", True), ("short\n", False), ("", False), ("x" * 129 + "\n", False)],
)
def test_read_new_password_from_stdin_enforces_policy(stdin, ok):
    result = subprocess.run(
        ["bash", "-c", f'source <(sed \'$d\' "{SCRIPT}")\nread_new_password false'],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) is ok, result.stderr
    if ok:
        assert result.stdout == stdin.rstrip("\n")


def test_read_new_password_generates_a_policy_compliant_password():
    result = _run("read_new_password true")
    assert result.returncode == 0
    assert len(result.stdout) >= 12


def test_hash_password_rejects_output_that_is_not_a_portal_hash():
    result = _run(
        "compose() { printf 'pbkdf2_sha256$600000$abc$def\\x27; DROP TABLE users; --'; }\n"
        "printf secret | hash_password"
    )
    assert result.returncode != 0
    assert "could not hash the password" in result.stderr


def test_hash_password_accepts_a_portal_hash():
    result = _run(
        "compose() { [[ $3 == true ]] || printf 'pbkdf2_sha256$600000$c2FsdA$ZGlnZXN0'; }\n"
        "printf secret | hash_password"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "pbkdf2_sha256$600000$c2FsdA$ZGlnZXN0"


@pytest.mark.parametrize("args", ["", "verify", "bogus a@example.com", "verify a@example.com b@example.com", "verify --x"])
def test_user_rejects_bad_usage_before_touching_docker(args):
    result = subprocess.run(
        ["bash", "-c", f'"{SCRIPT}" user {args}'],
        text=True,
        capture_output=True,
        env={**os.environ, "MYPORTAL_DIR": "/nonexistent"},
        check=False,
    )
    assert result.returncode != 0
    assert "usage: myportal-docker user verify USERNAME" in result.stderr


def _upgrade_harness(tmp_path, *, installed: str, latest: str, published: bool) -> str:
    """Shell prelude that stubs out Docker, GitHub and root checks for cmd_upgrade."""
    project_env = tmp_path / ".env"
    project_env.write_text(f"MYPORTAL_VERSION={installed}\n", encoding="utf-8")
    return (
        f'PROJECT_ENV="{project_env}"\n'
        "require_root() { :; }; require_installed() { :; }; ensure_docker() { :; }\n"
        f"latest_release_tag() {{ printf '%s' '{latest}'; }}\n"
        f"release_published() {{ echo \"published? $1\" >&2; {'true' if published else 'false'}; }}\n"
        'self_update() { echo "self_update $*"; }\n'
        # Called as image=$(obtain_image ...): report on stderr, then stop the run.
        'obtain_image() { echo "obtain_image $*" >&2; exit 3; }\n'
    )


def test_upgrade_refreshes_script_when_already_on_latest_release(tmp_path):
    result = _run(_upgrade_harness(tmp_path, installed="v0.6.4", latest="v0.6.4", published=True) + "cmd_upgrade --yes")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["self_update v0.6.4 upgrade --yes"]
    assert "already the latest release" in result.stderr


def test_upgrade_waits_until_release_is_fully_published(tmp_path):
    result = _run(_upgrade_harness(tmp_path, installed="v0.6.3", latest="v0.6.4", published=False) + "cmd_upgrade --yes")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""  # self_update did not run
    assert "obtain_image" not in result.stderr
    assert "still being published" in result.stderr


def test_upgrade_proceeds_once_release_is_published(tmp_path):
    result = _run(_upgrade_harness(tmp_path, installed="v0.6.3", latest="v0.6.4", published=True) + "cmd_upgrade --yes")
    assert result.stdout.splitlines() == ["self_update v0.6.4 upgrade --yes"]
    assert "obtain_image v0.6.4" in result.stderr


def test_explicit_upgrade_does_not_wait_for_publication(tmp_path):
    result = _run(
        _upgrade_harness(tmp_path, installed="v0.6.3", latest="v0.6.4", published=False)
        + "cmd_upgrade --version v0.6.4 --yes"
    )
    assert "published?" not in result.stderr
    assert "obtain_image v0.6.4" in result.stderr


@pytest.mark.parametrize("available", [True, False])
def test_release_published_checks_the_release_script_asset(tmp_path, available):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(f'#!/usr/bin/env bash\necho "$*" >> "$CALLS"\nexit {0 if available else 22}\n', encoding="utf-8")
    fake_curl.chmod(0o755)
    calls = tmp_path / "calls"
    result = _run('release_published v0.6.4', env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls)})
    assert (result.returncode == 0) is available
    assert "https://github.com/bradhawkins85/MyPortal/releases/download/v0.6.4/myportal-docker.sh" in calls.read_text()


@pytest.mark.parametrize("same", [True, False])
def test_self_update_installs_the_installed_release_script(tmp_path, same):
    published = "#!/usr/bin/env bash\necho new\n"
    installed = tmp_path / "myportal-docker"
    installed.write_text(published if same else "#!/usr/bin/env bash\necho old\n", encoding="utf-8")
    project_env = tmp_path / ".env"
    project_env.write_text("MYPORTAL_VERSION=v0.6.4\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    source = tmp_path / "published.sh"
    source.write_text(published, encoding="utf-8")
    fake_curl = bin_dir / "curl"
    # Honour "-o FILE" by copying the published script there.
    fake_curl.write_text(
        '#!/usr/bin/env bash\nwhile (($#)); do [[ $1 == -o ]] && cp "$SRC" "$2"; shift; done\n', encoding="utf-8"
    )
    fake_curl.chmod(0o755)

    result = _run(
        f'INSTALLED_SCRIPT="{installed}"; PROJECT_ENV="{project_env}"\n'
        "require_root() { :; }; require_installed() { :; }\n"
        "cmd_self_update",
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "SRC": str(source)},
    )

    assert result.returncode == 0, result.stderr
    assert installed.read_text(encoding="utf-8") == published
    assert ("already the version" in result.stderr) is same
