from __future__ import annotations

import os
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "provision_mysql.sh"


def _executable(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\nset -e\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _run(
    tmp_path: Path,
    env_contents: str,
    *,
    mariadb_version: str = "10.11.13",
    existing_server: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    sql = tmp_path / "sql"
    # Installing mariadb-server makes a mock server binary appear, reporting
    # the version the mocked package index offers.
    _executable(
        bin_dir / "apt-get",
        'echo "apt-get $*" >> "$CALLS"\n'
        'if [[ "$*" == *mariadb-server* ]]; then\n'
        '  printf \'#!/usr/bin/env bash\\necho "mariadbd  Ver %s-MariaDB for debian-linux-gnu"\\n\' "$MARIADB_VERSION" > "$BIN_DIR/mariadbd"\n'
        '  chmod +x "$BIN_DIR/mariadbd"\n'
        "fi",
    )
    _executable(
        bin_dir / "apt-cache",
        'echo "mariadb-server:"; echo "  Candidate: 1:${MARIADB_VERSION}-0ubuntu1"',
    )
    _executable(bin_dir / "systemctl", 'echo "systemctl $*" >> "$CALLS"')
    for client in ("mysql", "mariadb"):
        _executable(bin_dir / client, 'cat > "$SQL_OUTPUT"; echo mysql >> "$CALLS"')
    if existing_server:
        _executable(bin_dir / "mysqld", f'echo "{existing_server}"')
    # Keep Python and core shell utilities available while prioritising mocks.
    environment = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "SQL_OUTPUT": str(sql),
        "BIN_DIR": str(bin_dir),
        "MARIADB_VERSION": mariadb_version,
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
    assert "apt-get install -y -qq mariadb-server mariadb-client" in recorded_calls
    assert "systemctl enable --now mariadb" in recorded_calls
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


_LOCAL_DB = "DB_HOST=localhost\nDB_USER=myportal\nDB_PASSWORD=a-secure-db-password\nDB_NAME=myportal\n"


def test_refuses_mariadb_older_than_required_before_installing(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, _LOCAL_DB, mariadb_version="10.6.23")

    assert result.returncode != 0
    assert "requires MariaDB 10.10 or later" in result.stderr
    assert "install -y" not in (calls.read_text(encoding="utf-8") if calls.exists() else "")


def test_refuses_existing_oracle_mysql_server(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        _LOCAL_DB,
        existing_server="mysqld  Ver 8.0.43-0ubuntu0.24.04.1 for Linux on x86_64 ((Ubuntu))",
    )

    assert result.returncode != 0
    assert "Oracle MySQL" in result.stderr
    assert not (tmp_path / "sql").exists()
