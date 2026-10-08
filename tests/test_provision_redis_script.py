import os
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/provision_redis.sh"


def provision(tmp_path, contents, *, installed=False, packaged=False):
    env_file = tmp_path / "myportal.env"
    env_file.write_text(contents)
    env_file.chmod(0o640)
    # Stub all host mutations and installation detection, including when the
    # test machine itself happens to have Redis or an active systemd.
    program = f"""
command() {{
  if [[ "$1" == -v && "$2" == redis-server ]]; then return {0 if installed else 1}; fi
  if [[ "$1" == -v && "$2" == systemctl ]]; then return 1; fi
  builtin command "$@"
}}
dpkg-query() {{ printf '%s' '{'install ok installed' if packaged else ''}'; }}
apt-get() {{ echo "apt $*"; }}
env() {{ shift; "$@"; }}
service() {{ echo "service $*"; }}
source {shlex.quote(str(SCRIPT))} {shlex.quote(str(env_file))}
"""
    result = subprocess.run(
        ["bash", "-c", program], text=True, capture_output=True, env=os.environ.copy()
    )
    assert result.returncode == 0, result.stderr
    assert env_file.stat().st_mode & 0o777 == 0o640
    return result, env_file


@pytest.mark.parametrize("contents", ["DB_NAME=existing\n", "REDIS_URL=\n", 'export REDIS_URL = "" # unset\n'])
def test_missing_connection_installs_local_redis(tmp_path, contents):
    result, env_file = provision(tmp_path, contents)
    assert "REDIS_URL=redis://127.0.0.1:6379/0\n" in env_file.read_text()
    assert "apt install -y -qq redis-server" in result.stdout
    assert "service redis-server start" in result.stdout


@pytest.mark.parametrize("installed,packaged", [(True, False), (False, True)])
def test_existing_redis_is_started_without_installing(tmp_path, installed, packaged):
    contents = "REDIS_URL=redis://localhost:6379/3\n"
    result, env_file = provision(tmp_path, contents, installed=installed, packaged=packaged)
    assert "apt " not in result.stdout
    assert "service redis-server start" in result.stdout
    assert env_file.read_text() == contents


def test_remote_redis_keeps_configuration_and_skips_host_setup(tmp_path):
    contents = 'REDIS_URL="rediss://user:secret@cache.internal:6380/2"\n'
    result, env_file = provision(tmp_path, contents)
    assert result.stdout == ""
    assert env_file.read_text() == contents


def test_baremetal_install_and_upgrade_both_provision_redis():
    for name in ("install_environment.sh", "upgrade.sh"):
        assert '"${SCRIPT_DIR}/provision_redis.sh" "$ENV_FILE"' in (ROOT / "scripts" / name).read_text()
