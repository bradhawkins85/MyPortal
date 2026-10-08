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
    for command in ("install", "upgrade", "check", "backup", "restore-db", "auto-upgrade", "superadmin", "user verify", "setup"):
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
    app_env = tmp_path / "myportal.env"
    app_env.write_text("REDIS_URL=redis://redis:6379/0\n")
    return (
        f'PROJECT_ENV="{project_env}"\n'
        f'APP_ENV="{app_env}"\n'
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


# ---------------------------------------------------------------------------
# Blue/green slots
# ---------------------------------------------------------------------------
def test_help_lists_rollback():
    result = subprocess.run(["bash", str(SCRIPT), "help"], text=True, capture_output=True, check=False)
    assert "rollback" in result.stdout


def test_slot_helpers():
    result = _run(
        'printf "%s %s %s\\n" "$(other_slot blue)" "$(other_slot green)" "$(other_slot "")"\n'
        'printf "%s %s\\n" "$(slot_key blue IMAGE)" "$(slot_key green VERSION)"'
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["green blue blue", "BLUE_IMAGE GREEN_VERSION"]


@pytest.mark.parametrize(("slot", "service"), [("", "app"), ("blue", "app_blue"), ("green", "app_green")])
def test_app_service_follows_the_active_slot(tmp_path, slot, service):
    project_env = tmp_path / ".env"
    project_env.write_text(f"ACTIVE_SLOT={slot}\n", encoding="utf-8")
    result = _run(f'PROJECT_ENV="{project_env}"; app_service')
    assert result.stdout == service


def test_compose_file_publishes_the_port_only_on_the_proxy(tmp_path):
    yaml = pytest.importorskip("yaml")
    compose_file = tmp_path / "docker-compose.yml"
    result = _run(f'COMPOSE_FILE="{compose_file}"; write_compose_file')
    assert result.returncode == 0, result.stderr
    services = yaml.safe_load(compose_file.read_text(encoding="utf-8"))["services"]
    assert set(services) == {"db", "redis", "app_blue", "app_green", "proxy"}
    assert services["proxy"]["ports"] == ["${HTTP_BIND}:${HTTP_PORT}:8080"]
    for slot in ("blue", "green"):
        app = services[f"app_{slot}"]
        assert "ports" not in app
        assert app["image"] == "${" + slot.upper() + "_IMAGE}"
        assert app["environment"]["APP_INSTANCE_ID"] == slot
        assert app["environment"]["PORT"] == "8000"
        assert app["environment"]["TRUSTED_PROXIES"] == "${APP_TRUSTED_PROXIES}"
    assert services["app_blue"]["volumes"] == services["app_green"]["volumes"]
    assert "ports" not in services["redis"]
    assert services["redis"]["volumes"] == ["redis_data:/data"]
    assert services["redis"]["healthcheck"]["test"] == ["CMD", "redis-cli", "ping"]


@pytest.mark.parametrize("contents", ["", "REDIS_URL=\n", 'export REDIS_URL = ""\n', "# REDIS_URL=old\n"])
def test_redis_defaults_are_applied_once(tmp_path, contents):
    app_env = tmp_path / "myportal.env"
    app_env.write_text(contents + "DB_NAME=existing\n")
    app_env.chmod(0o600)
    project_env = tmp_path / ".env"
    project_env.write_text("")
    result = _run(
        f'APP_ENV="{app_env}"; PROJECT_ENV="{project_env}"\n'
        "ensure_redis_settings\nensure_redis_settings\n"
        "compose() { [[ $1 == ps ]] || echo \"$*\"; }\nensure_local_redis"
    )
    assert result.returncode == 0, result.stderr
    assert app_env.read_text().count("REDIS_URL=redis://redis:6379/0") == 1
    assert "DB_NAME=existing" in app_env.read_text()
    assert app_env.stat().st_mode & 0o777 == 0o600
    assert result.stderr.strip() == "up -d --no-recreate --pull missing --wait redis"


def test_configured_external_redis_is_preserved_and_not_installed(tmp_path):
    app_env = tmp_path / "myportal.env"
    contents = 'export REDIS_URL = "rediss://user:secret@cache.internal:6380/2"\n'
    app_env.write_text(contents)
    project_env = tmp_path / ".env"
    project_env.write_text("REDIS_IMAGE=custom:7\n")
    result = _run(
        f'APP_ENV="{app_env}"; PROJECT_ENV="{project_env}"\n'
        "ensure_redis_settings\ncompose() { exit 99; }\nensure_local_redis"
    )
    assert result.returncode == 0, result.stderr
    assert app_env.read_text() == contents
    assert project_env.read_text() == "REDIS_IMAGE=custom:7\n"


def test_existing_redis_container_is_never_pulled_or_recreated(tmp_path):
    app_env = tmp_path / "myportal.env"
    app_env.write_text("REDIS_URL=redis://redis:6379/0\n")
    result = _run(
        f'APP_ENV="{app_env}"\n'
        'compose() { if [[ $1 == ps ]]; then echo existing-container; else echo "$*"; fi; }\n'
        "ensure_local_redis"
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == "up -d --no-recreate --pull never --wait redis"


def test_upgrade_applies_redis_defaults_even_on_current_release(tmp_path):
    harness = _upgrade_harness(tmp_path, installed="v1", latest="v1", published=True)
    (tmp_path / "myportal.env").write_text("REDIS_URL=\n")
    result = _run(harness + 'deploy_release() { echo "deploy $2"; }\ncmd_upgrade --yes')
    assert result.returncode == 0, result.stderr
    assert "deploy v1" in result.stdout


def test_proxy_config_applies_only_valid_trusted_proxies(tmp_path):
    app_env = tmp_path / "myportal.env"
    app_env.write_text(
        'TRUSTED_PROXIES="10.0.0.5, 192.168.0.0/16,fd00::/8, evil; include /etc/passwd,host.example"\n',
        encoding="utf-8",
    )
    proxy_dir = tmp_path / "proxy"
    result = _run(f'APP_ENV="{app_env}"; PROXY_DIR="{proxy_dir}"; write_proxy_config green')
    assert result.returncode == 0, result.stderr
    config = (proxy_dir / "myportal.conf").read_text(encoding="utf-8")
    for entry in ("10.0.0.5", "192.168.0.0/16", "fd00::/8"):
        assert f"set_real_ip_from {entry};" in config
        assert f"    {entry} 1;" in config
    assert "evil" not in config and "host.example" not in config
    assert config.count("set_real_ip_from") == 3
    assert "ignoring TRUSTED_PROXIES entry 'evil;include/etc/passwd'" in result.stderr
    # The application only ever sees the address the proxy determined.
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in config
    assert (proxy_dir / "active-slot.inc").read_text(encoding="utf-8").splitlines()[-1] == (
        "server app_green:8000 resolve;"
    )
    assert (proxy_dir / "myportal-unavailable.html").is_file()
    assert oct(proxy_dir.stat().st_mode & 0o777) == oct(0o755)


def test_proxy_config_keeps_the_active_slot(tmp_path):
    proxy_dir = tmp_path / "proxy"
    proxy_dir.mkdir()
    (proxy_dir / "active-slot.inc").write_text("server app_blue:8000 resolve;\n", encoding="utf-8")
    result = _run(f'APP_ENV="{tmp_path}/missing.env"; PROXY_DIR="{proxy_dir}"; write_proxy_config green')
    assert result.returncode == 0, result.stderr
    assert "app_blue" in (proxy_dir / "active-slot.inc").read_text(encoding="utf-8")
    assert "set_real_ip_from" not in (proxy_dir / "myportal.conf").read_text(encoding="utf-8")


def _deploy_harness(tmp_path, *, ready: bool) -> str:
    """Stub Docker for deploy_release; the green slot is idle, blue serves v1."""
    project_env = tmp_path / ".env"
    project_env.write_text(
        "MYPORTAL_VERSION=v1\nMYPORTAL_IMAGE=img:v1\nACTIVE_SLOT=blue\n"
        "BLUE_IMAGE=img:v1\nGREEN_IMAGE=img:v0\nBLUE_VERSION=v1\nGREEN_VERSION=v0\n",
        encoding="utf-8",
    )
    (tmp_path / "myportal.env").write_text("DB_NAME=myportal\n")
    calls = tmp_path / "calls"
    return (
        f'PROJECT_ENV="{project_env}"; COMPOSE_FILE="{tmp_path}/docker-compose.yml"\n'
        f'APP_ENV="{tmp_path}/myportal.env"; PROXY_DIR="{tmp_path}/proxy"; MYPORTAL_DRAIN_SECONDS=0\n'
        f'compose() {{ echo "compose $*" >> "{calls}"; }}\n'
        f'switch_proxy() {{ echo "switch_proxy $*" >> "{calls}"; }}\n'
        "refresh_app_trusted_proxies() { :; }\n"
        "wait_for_proxy() { :; }\n"
        f"wait_for_service() {{ {'true' if ready else 'false'}; }}\n"
        "service_container() { :; }\n"
    )


def test_deploy_switches_to_the_idle_slot_once_ready(tmp_path):
    result = _run(_deploy_harness(tmp_path, ready=True) + "deploy_release img:v2 v2")
    assert result.returncode == 0, result.stderr
    settings = (tmp_path / ".env").read_text(encoding="utf-8")
    for line in ("ACTIVE_SLOT=green", "GREEN_IMAGE=img:v2", "GREEN_VERSION=v2", "MYPORTAL_VERSION=v2",
                 "MYPORTAL_IMAGE=img:v2", "BLUE_IMAGE=img:v1", "BLUE_VERSION=v1"):
        assert line in settings.splitlines()
    calls = (tmp_path / "calls").read_text(encoding="utf-8").splitlines()
    assert "compose up -d --no-deps --force-recreate app_green" in calls
    assert calls.index("switch_proxy green") < calls.index("compose stop app_blue")


def test_deploy_leaves_the_serving_slot_alone_when_the_release_fails(tmp_path):
    result = _run(_deploy_harness(tmp_path, ready=False) + "deploy_release img:v2 v2")
    assert result.returncode != 0
    settings = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    for line in ("ACTIVE_SLOT=blue", "GREEN_IMAGE=img:v0", "MYPORTAL_VERSION=v1"):
        assert line in settings
    calls = (tmp_path / "calls").read_text(encoding="utf-8").splitlines()
    assert not any(call.startswith("switch_proxy") for call in calls)
    assert "compose stop app_green" in calls
    assert "compose stop app_blue" not in calls


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ("MYPORTAL_VERSION=v1\n", "no previous release is kept yet"),
        ("MYPORTAL_VERSION=v2\nACTIVE_SLOT=blue\nGREEN_VERSION=v2\nGREEN_IMAGE=img:v2\n", "no previous release is kept"),
        ("MYPORTAL_VERSION=v2\nACTIVE_SLOT=blue\nGREEN_VERSION=\n", "no previous release is kept"),
    ],
)
def test_rollback_refuses_without_a_previous_release(tmp_path, settings, message):
    project_env = tmp_path / ".env"
    project_env.write_text(settings, encoding="utf-8")
    result = _run(
        f'PROJECT_ENV="{project_env}"\n'
        "require_root() { :; }; require_installed() { :; }; ensure_docker() { :; }\n"
        "deploy_release() { echo deployed; }\n"
        "cmd_rollback --yes"
    )
    assert result.returncode != 0
    assert message in result.stderr
    assert "deployed" not in result.stdout


def test_rollback_switches_to_the_kept_release_and_skips_the_newer_one(tmp_path):
    project_env = tmp_path / ".env"
    project_env.write_text(
        "MYPORTAL_VERSION=v2\nACTIVE_SLOT=blue\nGREEN_VERSION=v1\nGREEN_IMAGE=img:v1\n", encoding="utf-8"
    )
    result = _run(
        f'PROJECT_ENV="{project_env}"\n'
        "require_root() { :; }; require_installed() { :; }; ensure_docker() { :; }\n"
        "docker() { [[ $1 == image ]]; }\n"
        'deploy_release() { echo "deploy_release $*"; }\n'
        "cmd_rollback --yes"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["deploy_release img:v1 v1"]
    assert "FAILED_VERSION=v2" in project_env.read_text(encoding="utf-8").splitlines()


def test_conversion_stops_when_the_override_changes_the_old_app_service(tmp_path):
    (tmp_path / "docker-compose.override.yml").write_text(
        "services:\n  app:\n    labels:\n      - traefik.enable=true\n", encoding="utf-8"
    )
    harness = _deploy_harness(tmp_path, ready=True)
    project_env = tmp_path / ".env"
    project_env.write_text("MYPORTAL_VERSION=v1\nMYPORTAL_IMAGE=img:v1\n", encoding="utf-8")
    result = _run(harness + f'MYPORTAL_DIR="{tmp_path}"\ndeploy_release img:v2 v2')
    assert result.returncode != 0
    assert "changes the 'app' service" in result.stderr
    assert not (tmp_path / "calls").exists()
    assert project_env.read_text(encoding="utf-8") == "MYPORTAL_VERSION=v1\nMYPORTAL_IMAGE=img:v1\n"


def test_readiness_probe_ignores_http_proxy_settings():
    # An HTTP_PROXY in myportal.env (or injected by Docker) must not route the
    # in-container check of 127.0.0.1 through the proxy, where it never succeeds.
    import http.server
    import socket
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            body = b'{"status":"ok","version":"v1"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    try:
        server = http.server.HTTPServer(("127.0.0.1", 8000), Handler)
    except OSError:
        pytest.skip("port 8000 is in use")
    with socket.socket() as blackhole:
        blackhole.bind(("127.0.0.1", 0))
        proxy = f"http://127.0.0.1:{blackhole.getsockname()[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        program = _run('printf "%s" "$READYZ_PROGRAM"').stdout
        result = subprocess.run(
            ["python3", "-c", program],
            text=True,
            capture_output=True,
            env={**os.environ, "HTTP_PROXY": proxy, "http_proxy": proxy, "NO_PROXY": "", "no_proxy": ""},
            timeout=30,
            check=False,
        )
    finally:
        server.shutdown()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '{"status":"ok","version":"v1"}'


def test_switch_proxy_replaces_a_stopped_proxy_without_reloading_it(tmp_path):
    calls = tmp_path / "calls"
    state = tmp_path / "started"
    result = _run(
        f'PROXY_DIR="{tmp_path}"\n'
        # No proxy runs until "compose up" starts one.
        f'compose() {{ echo "compose $*" >> "{calls}"; touch "{state}"; }}\n'
        f'service_container() {{ [[ -f "{state}" ]] && echo proxy-id; }}\n'
        f'docker() {{ echo "docker $*" >> "{calls}"; [[ $1 == inspect ]] && echo "proxy-id now"; }}\n'
        "switch_proxy green"
    )
    assert result.returncode == 0, result.stderr
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert "compose up -d --no-deps --force-recreate proxy" in lines
    assert not any("reload" in line for line in lines)
    assert "app_green" in (tmp_path / "active-slot.inc").read_text(encoding="utf-8")


def test_switch_proxy_reloads_a_running_proxy(tmp_path):
    calls = tmp_path / "calls"
    result = _run(
        f'PROXY_DIR="{tmp_path}"\n'
        f'compose() {{ echo "compose $*" >> "{calls}"; }}\n'
        "service_container() { echo proxy-id; }\n"
        f'docker() {{ echo "docker $*" >> "{calls}"; [[ $1 == inspect ]] && echo "proxy-id then"; return 0; }}\n'
        "switch_proxy blue"
    )
    assert result.returncode == 0, result.stderr
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert "compose up -d --no-deps proxy" in lines
    assert "docker exec proxy-id nginx -s reload" in lines


def _compose_download_harness(tmp_path, *, checksum: str) -> str:
    # Stub curl: resolve "latest" to v2.40.0 and serve the binary and checksum.
    return (
        f'BINARY_BODY="compose-binary"; CHECKSUM_BODY="{checksum}"\n'
        "curl() {\n"
        '  local out="" url="" arg\n'
        '  while (($#)); do\n'
        '    case "$1" in -o) out="$2"; shift 2;; -w) shift 2;; -*) shift;; *) url="$1"; shift;; esac\n'
        "  done\n"
        '  case "$url" in\n'
        '    */latest) printf "%s" "https://github.com/docker/compose/releases/tag/v2.40.0";;\n'
        '    */download/v2.40.0/docker-compose-linux-x86_64) printf "%s" "$BINARY_BODY" >"$out";;\n'
        '    */download/v2.40.0/docker-compose-linux-x86_64.sha256) printf "%s\\n" "$CHECKSUM_BODY" >"$out";;\n'
        "    *) return 22;;\n"
        "  esac\n"
        "}\n"
        f'install_compose_binary "{tmp_path}/plugins" x86_64\n'
    )


def test_compose_binary_installs_only_when_checksum_matches(tmp_path):
    import hashlib

    digest = hashlib.sha256(b"compose-binary").hexdigest()
    result = _run(_compose_download_harness(tmp_path, checksum=f"{digest} *docker-compose-linux-x86_64"))
    assert result.returncode == 0, result.stderr
    installed = tmp_path / "plugins" / "docker-compose"
    assert installed.read_bytes() == b"compose-binary"
    assert os.access(installed, os.X_OK)


@pytest.mark.parametrize("checksum", ["0" * 64 + " *docker-compose-linux-x86_64", "", "not-a-checksum"])
def test_compose_binary_fails_closed_on_checksum_mismatch(tmp_path, checksum):
    result = _run(_compose_download_harness(tmp_path, checksum=checksum))
    assert result.returncode != 0
    assert "Checksum verification failed" in result.stderr
    assert not (tmp_path / "plugins" / "docker-compose").exists()
