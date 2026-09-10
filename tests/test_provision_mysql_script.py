from __future__ import annotations

import os
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "provision_mysql.sh"


def _executable(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\nset -e\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _run(tmp_path: Path, env_contents: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    sql = tmp_path / "sql"
    _executable(bin_dir / "apt-get", 'echo "apt-get $*" >> "$CALLS"')
    _executable(bin_dir / "systemctl", 'echo "systemctl $*" >> "$CALLS"')
    _executable(bin_dir / "mysql", 'cat > "$SQL_OUTPUT"; echo mysql >> "$CALLS"')
    # Keep Python and core shell utilities available while prioritising mocks.
    environment = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "SQL_OUTPUT": str(sql),
    }
    env_file = tmp_path / ".env"
    env_file.write_text(env_contents, encoding="utf-8")
    result = subprocess.run(
        ["bash", str(SCRIPT), str(env_file)],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    return result, calls


def test_provisions_missing_local_server_and_database(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "DB_HOST=localhost\nDB_USER=myportal\nDB_PASSWORD=a-secure-db-password\nDB_NAME=myportal\n",
    )

    assert result.returncode == 0, result.stderr
    recorded_calls = calls.read_text(encoding="utf-8")
    assert "apt-get install -y -qq default-mysql-server" in recorded_calls
    assert "systemctl enable --now mysql" in recorded_calls
    sql = (tmp_path / "sql").read_text(encoding="utf-8")
    assert "CREATE DATABASE IF NOT EXISTS `myportal`" in sql
    assert "'myportal'@'localhost'" in sql
    assert "GRANT ALL PRIVILEGES ON `myportal`.*" in sql


def test_skips_server_provisioning_for_remote_database(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "DB_HOST=db.example.test\nDB_USER=myportal\nDB_PASSWORD=secret\nDB_NAME=myportal\n",
    )

    assert result.returncode == 0
    assert "not local" in result.stderr
    assert not calls.exists()


def test_rejects_unsafe_database_identifier(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "DB_HOST=localhost\nDB_USER=myportal\nDB_PASSWORD=secret\nDB_NAME=myportal`; DROP DATABASE x\n",
    )

    assert result.returncode != 0
    assert "DB_USER and DB_NAME" in result.stderr
    assert not calls.exists()
