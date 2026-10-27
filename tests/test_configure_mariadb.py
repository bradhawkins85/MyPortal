"""Local database tuning must respect memory limits and preserve server settings."""
import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("configure_mariadb", ROOT / "scripts/configure_mariadb.py")
tuning = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tuning)


@pytest.mark.parametrize("ram_gib,limit,expected_mib", [
    (16, "max", 4096), (64, str(2 * 1024 ** 3), 512),
    (2, "max", 512), (64, "max", 4096),
])
def test_memory_aware_default_respects_container_limit(tmp_path, ram_gib, limit, expected_mib):
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "meminfo").write_text("MemTotal: " + str(ram_gib * 1024 ** 2) + " kB\n")
    (proc / "self/cgroup").write_text("0::/nested\n")
    cgroup = tmp_path / "cgroup"
    (cgroup / "nested").mkdir(parents=True)
    (cgroup / "memory.max").write_text(limit)
    (cgroup / "nested/memory.max").write_text("max")
    assert tuning.default_pool_size(proc, cgroup) == expected_mib * tuning.MIB


def test_config_update_keeps_other_settings_and_is_idempotent():
    original = "[mysqld]\nbind-address = 127.0.0.1\n# innodb_buffer_pool_size = 128M\ninnodb_buffer_pool_size = 128M\n[client]\nport = 3306\n"
    updated = tuning.updated_config(original, 4 * tuning.GIB)
    assert "bind-address = 127.0.0.1" in updated and "[client]\nport = 3306" in updated
    assert tuning.pool_settings(updated) == [(len(updated.splitlines()) - 1, 4 * tuning.GIB)]
    assert tuning.updated_config(updated, 4 * tuning.GIB) == updated


def _environment(tmp_path, monkeypatch, *, host="localhost", running=128 * tuning.MIB, configured="128M"):
    env_file = tmp_path / "application.env"
    env_file.write_text("DB_HOST='" + host + "'\nDB_PORT=3306\n")
    config = tmp_path / "50-server.cnf"
    config.write_text("[mysqld]\nmax_connections = 150\ninnodb_buffer_pool_size = " + configured + "\n")
    config.chmod(0o640)
    monkeypatch.setattr(tuning, "default_pool_size", lambda: 4 * tuning.GIB)
    monkeypatch.setattr(tuning.shutil, "which", lambda _: "/usr/bin/mariadb")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["input"]))
        statement = kwargs["input"]
        output = ""
        if statement.startswith("SELECT @@version"):
            output = "10.11.13-MariaDB\t3306\t" + str(running)
        elif statement.startswith("SELECT @@innodb"):
            output = str(max(running, tuning.size_bytes(configured), 4 * tuning.GIB))
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(tuning.subprocess, "run", run)
    return env_file, config, calls


def test_sets_live_and_persistent_pool_without_restarting_database(tmp_path, monkeypatch):
    env, config, calls = _environment(tmp_path, monkeypatch)
    tuning.configure(env, config)
    assert any(sql.startswith("SET GLOBAL innodb_buffer_pool_size = 4294967296") for _, sql in calls)
    assert tuning.pool_settings(config.read_text())[0][1] == 4 * tuning.GIB
    assert "max_connections = 150" in config.read_text()
    assert config.stat().st_mode & 0o777 == 0o640
    assert all(command[0] == "/usr/bin/mariadb" and "--protocol=socket" in command for command, _ in calls)


@pytest.mark.parametrize("running,configured", [(8 * tuning.GIB, "128M"), (128 * tuning.MIB, "8G")])
def test_preserves_larger_existing_values(tmp_path, monkeypatch, running, configured):
    env, config, calls = _environment(tmp_path, monkeypatch, running=running, configured=configured)
    tuning.configure(env, config)
    assert tuning.pool_settings(config.read_text())[0][1] == 8 * tuning.GIB
    assert not any("= 4294967296" in sql for _, sql in calls)


def test_remote_database_does_not_touch_local_configuration(tmp_path, monkeypatch):
    env, config, calls = _environment(tmp_path, monkeypatch, host="db.example.test")
    original = config.read_bytes()
    tuning.configure(env, config)
    assert not calls and config.read_bytes() == original


def test_failed_live_resize_does_not_persist_configuration(tmp_path, monkeypatch):
    env, config, _ = _environment(tmp_path, monkeypatch)
    original = config.read_bytes()
    run = tuning.subprocess.run

    def fail(command, **kwargs):
        if kwargs["input"].startswith("SET GLOBAL"):
            raise RuntimeError("resize failed")
        return run(command, **kwargs)

    monkeypatch.setattr(tuning.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="resize failed"):
        tuning.configure(env, config)
    assert config.read_bytes() == original


def test_install_and_upgrade_apply_tuning_before_migrations():
    install = (ROOT / "scripts/install_environment.sh").read_text()
    assert 'python3 "${SCRIPT_DIR}/configure_mariadb.py" "$ENV_FILE"' in install
    upgrade = (ROOT / "scripts/upgrade.sh").read_text()
    assert 'git show "${revision}:scripts/configure_mariadb.py"' in upgrade
    assert upgrade.index('configure_mariadb_for_revision "$TARGET_REVISION"') < upgrade.index('run_migration_phase "$RELEASE_DIR"')


@pytest.mark.parametrize("raw,expected", [
    ("plain#hash", "plain#hash"), ("plain # comment", "plain"),
    ('"space $dollar #hash !bang"', "space $dollar #hash !bang"),
    ("'single\\'quote\\\\slash'", "single'quote\\slash"),
    ('"line\\nquote\\\"slash\\\\end"', 'line\nquote"slash\\end'),
])
def test_credentials_keep_dotenv_special_characters(tmp_path, raw, expected):
    path = tmp_path / "application.env"
    path.write_text("DB_PASSWORD=" + raw + "\nDB_HOST=localhost\n")
    settings = tuning.read_database_settings(path)
    assert settings["DB_PASSWORD"] == expected
    assert settings["DB_HOST"] == "localhost"


def test_root_login_failure_uses_private_configured_credentials(tmp_path, monkeypatch):
    env, config, calls = _environment(tmp_path, monkeypatch)
    secret = 'space $dollar #hash !bang "quote" \\slash'
    env.write_text("DB_HOST=localhost\nDB_PORT=3306\nDB_USER=portal_admin\nDB_PASSWORD='" + secret + "'\n")
    original_run = tuning.subprocess.run
    private_files = []

    def run(command, **kwargs):
        if "--protocol=socket" in command:
            raise subprocess.CalledProcessError(1, command, stderr="ERROR 1045: Access denied for root")
        assert command[1].startswith("--defaults-file=")
        path = Path(command[1].split("=", 1)[1])
        private_files.append(path)
        assert path.stat().st_mode & 0o777 == 0o600
        text = path.read_text()
        assert 'user="portal_admin"' in text
        assert "password=" + tuning.option_value(secret) in text
        assert secret not in " ".join(command)
        return original_run(command, **kwargs)

    monkeypatch.setattr(tuning.subprocess, "run", run)
    tuning.configure(env, config)
    assert private_files and all(not path.exists() for path in private_files)
    assert tuning.pool_settings(config.read_text())[0][1] == 4 * tuning.GIB


def test_failed_authentication_reports_redacted_diagnostic_and_keeps_config(tmp_path, monkeypatch):
    env, config, _ = _environment(tmp_path, monkeypatch)
    env.write_text("DB_HOST=localhost\nDB_USER=portal_admin\nDB_PASSWORD=private-secret\n")
    original = config.read_bytes()
    private_files = []

    def fail(command, **kwargs):
        if command[1].startswith("--defaults-file="):
            private_files.append(Path(command[1].split("=", 1)[1]))
        raise subprocess.CalledProcessError(1, command, stderr="ERROR 1045: Access denied private-secret")

    monkeypatch.setattr(tuning.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="ERROR 1045") as error:
        tuning.configure(env, config)
    assert "private-secret" not in str(error.value)
    assert config.read_bytes() == original
    assert private_files and all(not path.exists() for path in private_files)


def test_optional_tuning_failure_does_not_abort_the_upgrade(tmp_path):
    upgrade = (ROOT / "scripts/upgrade.sh").read_text()
    invocation = upgrade[upgrade.index('\nconfigure_mariadb_for_revision "$TARGET_REVISION"'):]
    invocation = invocation[:invocation.index("\nredis_env_before=")]
    result = subprocess.run(
        ["bash", "-c", 'set -Eeuo pipefail\nTARGET_REVISION=test\n'
         'configure_mariadb_for_revision() { echo "ERROR 1045: Access denied" >&2; return 1; }\n'
         + invocation + '\nprintf "upgrade-continued\\n"\n'],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0
    assert "upgrade-continued" in result.stdout
    assert "ERROR 1045" in result.stderr and "continuing upgrade" in result.stderr
