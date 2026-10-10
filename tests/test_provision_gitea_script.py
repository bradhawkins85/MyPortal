"""Tests for scripts/provision_gitea.sh (bare-metal Gitea for RMM scripts).

Host mutations (systemd, users, downloads, the gitea CLI) are stubbed; these
tests cover the decisions and the files the script writes.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/provision_gitea.sh"


def run(snippet: str, env_file: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    program = f"source {shlex.quote(str(SCRIPT))}\nENV_FILE={shlex.quote(str(env_file))}\n{snippet}"
    return subprocess.run(
        ["bash", "-c", program], text=True, capture_output=True, env={**os.environ, **(env or {})}, check=False
    )


def env_file(tmp_path: Path, contents: str) -> Path:
    path = tmp_path / "myportal.env"
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o640)
    return path


@pytest.mark.parametrize(
    ("contents", "managed"),
    [
        ("", True),
        ("GITEA_BASE_URL=\n", True),
        ("GITEA_BASE_URL=http://127.0.0.1:3000/\n", True),
        ("GITEA_PROVISION=false\n", False),
        ('export GITEA_PROVISION="off"\n', False),
        ("GITEA_BASE_URL=https://git.example.com\n", False),
    ],
)
def test_only_a_local_or_unset_gitea_is_managed(tmp_path, contents, managed):
    result = run("managed_gitea", env_file(tmp_path, contents))
    assert (result.returncode == 0) is managed, result.stderr


def test_external_gitea_is_left_alone(tmp_path):
    contents = "GITEA_BASE_URL=https://git.example.com\nGITEA_API_TOKEN=keep\n"
    path = env_file(tmp_path, contents)
    result = subprocess.run(["bash", str(SCRIPT), str(path)], text=True, capture_output=True, check=False)
    assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    assert path.read_text(encoding="utf-8") == contents


def test_env_defaults_fill_only_missing_values(tmp_path):
    path = env_file(tmp_path, "GITEA_BASE_URL=\nGITEA_SCRIPTS_BRANCH=release # pinned\nDB_NAME=x\n")
    result = run(
        "env_default GITEA_BASE_URL http://127.0.0.1:3000\n"
        "env_default GITEA_BASE_URL http://other\n"
        "env_default GITEA_SCRIPTS_BRANCH main\n"
        "env_default GITEA_PUBLIC_URL /gitea\n",
        path,
    )
    assert result.returncode == 0, result.stderr
    assert path.read_text(encoding="utf-8") == (
        "GITEA_BASE_URL=http://127.0.0.1:3000\nGITEA_SCRIPTS_BRANCH=release # pinned\nDB_NAME=x\n"
        "GITEA_PUBLIC_URL=/gitea\n"
    )
    assert path.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize(
    ("portal", "expected"),
    [
        ("https://portal.example.com/", "https://portal.example.com/gitea/"),
        ('"https://portal.example.com:8443/app"', "https://portal.example.com:8443/gitea/"),
        ("", "http://myhost/gitea/"),
    ],
)
def test_root_url_follows_the_portal_address(tmp_path, portal, expected):
    result = run("hostname() { echo myhost; }\nroot_url", env_file(tmp_path, f"PORTAL_URL={portal}\n"))
    assert result.stdout == expected


def test_ini_set_changes_only_what_differs(tmp_path):
    config = tmp_path / "app.ini"
    config.write_text("APP_NAME = x\n\n[server]\nROOT_URL = http://old/gitea/\nHTTP_PORT = 3000\n\n[log]\nMODE = console\n")
    result = run(
        f'GITEA_CONFIG="{config}"\n'
        'printf "[%s]" "$(ini_set server ROOT_URL https://new/gitea/)"\n'
        'printf "[%s]" "$(ini_set server ROOT_URL https://new/gitea/)"\n'
        'printf "[%s]" "$(ini_set log LEVEL Info)"\n',
        env_file(tmp_path, ""),
    )
    assert result.stdout == "[changed][][changed]", result.stderr
    assert config.read_text() == (
        "APP_NAME = x\n\n[server]\nROOT_URL = https://new/gitea/\nHTTP_PORT = 3000\n\n[log]\nMODE = console\nLEVEL = Info\n"
    )


def _fake_download(tmp_path: Path, checksum: str | None) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    payload = "#!/bin/sh\necho 'Gitea version 9.9.9 built with GNU Make'\n"
    digest = checksum or hashlib.sha256(payload.encode()).hexdigest()
    (bin_dir / "curl").write_text(
        "#!/usr/bin/env bash\n"
        'out=""; url=""\n'
        'while (($#)); do case "$1" in -o) out="$2"; shift 2 ;; -*) shift ;; *) url="$1"; shift ;; esac; done\n'
        'echo "$url" >> "$CALLS"\n'
        f'if [[ "$url" == *.sha256 ]]; then printf "%s  gitea\\n" {digest} > "$out"; else printf %s {shlex.quote(payload)} > "$out"; fi\n'
    )
    (bin_dir / "curl").chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(tmp_path / "calls")}


def test_binary_is_installed_when_its_checksum_matches(tmp_path):
    target = tmp_path / "gitea"
    env = _fake_download(tmp_path, None) | {"MYPORTAL_GITEA_BIN": str(target), "MYPORTAL_GITEA_VERSION": "9.9.9"}
    result = run('uname() { echo x86_64; }\ninstall_binary && installed_version', env_file(tmp_path, ""), env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "9.9.9"
    assert "https://dl.gitea.com/gitea/9.9.9/gitea-9.9.9-linux-amd64" in (tmp_path / "calls").read_text()


@pytest.mark.parametrize("checksum", ["0" * 64, "not-a-checksum"])
def test_binary_is_not_installed_on_checksum_mismatch(tmp_path, checksum):
    target = tmp_path / "gitea"
    env = _fake_download(tmp_path, checksum) | {"MYPORTAL_GITEA_BIN": str(target)}
    result = run('uname() { echo aarch64; }\ninstall_binary', env_file(tmp_path, ""), env=env)
    assert result.returncode != 0
    assert not target.exists()
    assert "checksum" in result.stderr


def test_first_run_creates_admin_repository_and_token(tmp_path):
    path = env_file(tmp_path, "GITEA_BASE_URL=\nGITEA_API_TOKEN=\nPORTAL_URL=https://portal.example.com\n")
    calls = tmp_path / "calls"
    token = "a" * 40
    result = run(
        f'GITEA_ADMIN_FILE="{tmp_path}/admin-credentials"\n'
        f'gitea_cli() {{ echo "cli $*" >> "{calls}"\n'
        '  case "$*" in\n'
        '    *"user list"*) printf "ID Username Email IsActive IsAdmin\\n1 someone a@b true true\\n" ;;\n'
        "    *\"user create\"*) echo \"generated random password is 'p\\\"w\\\\d'\" ;;\n"
        f'    *generate-access-token*) echo "Access token was successfully created: {token}" ;;\n'
        "  esac; }\n"
        f'gitea_api() {{ echo "api $* stdin=$(cat)" >> "{calls}"; if [[ $1 == GET ]]; then echo 404; else echo 201; fi; }}\n'
        "connect_myportal",
        path,
    )
    assert result.returncode == 0, result.stderr
    settings = path.read_text(encoding="utf-8")
    for line in (f"GITEA_API_TOKEN={token}", "GITEA_BASE_URL=http://127.0.0.1:3000", "GITEA_PUBLIC_URL=/gitea",
                 "GITEA_SCRIPTS_REPOSITORY=myportal/rmm-scripts", "GITEA_SCRIPTS_BRANCH=main"):
        assert line in settings.splitlines()
    log = calls.read_text()
    assert "--scopes write:repository" in log
    assert "--random-password" in log
    # Secrets travel on stdin, escaped for curl's config syntax, never as arguments.
    assert f'stdin=header = "Authorization: token {token}"' in log
    assert 'stdin=user = "myportal:p\\"w\\\\d"' in log
    assert "api POST /user/repos" in log and '"private":true' in log
    credentials = tmp_path / "admin-credentials"
    assert credentials.stat().st_mode & 0o777 == 0o600
    assert credentials.read_text() == 'url=https://portal.example.com/gitea/\nusername=myportal\npassword=p"w\\d\n'


def test_connected_installation_keeps_its_token(tmp_path):
    contents = "GITEA_API_TOKEN=existing\nGITEA_BASE_URL=http://127.0.0.1:3000\nGITEA_PUBLIC_URL=/gitea\n" \
        "GITEA_SCRIPTS_REPOSITORY=team/scripts\nGITEA_SCRIPTS_BRANCH=main\n"
    path = env_file(tmp_path, contents)
    result = run("gitea_cli() { exit 99; }\ngitea_api() { exit 99; }\nconnect_myportal", path)
    assert result.returncode == 0, result.stderr
    assert path.read_text(encoding="utf-8") == contents


def test_baremetal_upgrade_provisions_gitea_without_blocking_and_nginx_serves_it():
    upgrade = (ROOT / "scripts/upgrade.sh").read_text()
    call = upgrade.index('bash "${SCRIPT_DIR}/provision_gitea.sh" "$ENV_FILE" \\\n  || echo')
    # Before the env checksum, so new Gitea settings reload the workers.
    assert upgrade.index('bash "${SCRIPT_DIR}/provision_redis.sh"') < call < upgrade.index("redis_env_after=")
    nginx = (ROOT / "deploy/nginx/myportal-bluegreen.conf").read_text()
    assert "location ^~ /gitea/ {\n    proxy_pass http://127.0.0.1:3000/;" in nginx


def test_gitea_never_searches_above_its_data_for_a_repository(tmp_path):
    # A stray /.git on the host otherwise stops Gitea starting ("invalid gitfile format: /.git").
    unit = tmp_path / "gitea.service"
    result = run(f'GITEA_UNIT="{unit}"\nwrite_unit', env_file(tmp_path, ""))
    assert result.returncode == 0, result.stderr
    assert "Environment=GIT_CEILING_DIRECTORIES=/var/lib\n" in unit.read_text()
    calls = tmp_path / "calls"
    result = run(
        f'GITEA_HOME="{tmp_path}"\nGITEA_GIT_CEILING=/ceiling\n'
        f'runuser() {{ echo "$PWD $*" > "{calls}"; }}\ngitea_cli admin user list',
        env_file(tmp_path, ""),
    )
    assert result.returncode == 0, result.stderr
    assert calls.read_text().startswith(f"{tmp_path} -u gitea -- env ")
    assert "GIT_CEILING_DIRECTORIES=/ceiling" in calls.read_text()
