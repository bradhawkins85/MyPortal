"""Local database tuning must respect memory limits and preserve server settings."""
import importlib.util
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
