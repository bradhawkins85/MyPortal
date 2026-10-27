#!/usr/bin/env python3
"""Persist and apply a memory-aware buffer pool default for local MariaDB."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import sys
from typing import Callable


MIB = 1024 ** 2
GIB = 1024 ** 3
SERVER_GROUP = re.compile(r"^(?:server|mysqld|mariadb|mariadbd)(?:-[\d.]+)?$")
POOL_SETTING = re.compile(r"^\s*innodb[-_]buffer[-_]pool[-_]size\s*=\s*([^#;]+)", re.I)


def read_database_settings(path: Path) -> dict[str, str]:
    # Match dotenv quoting without evaluating shell code or interpolating $.
    pattern = re.compile(
        r'''^[ \t]*(?:export[ \t]+)?(DB_HOST|DB_PORT|DB_USER|DB_PASSWORD)[ \t]*=[ \t]*'''
        r'''('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|[^\r\n]*)''', re.M | re.S,
    )
    escapes = {"\\": "\\", "'": "'", '"': '"', "a": "\a", "b": "\b",
               "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
    settings = {}
    for match in pattern.finditer(path.read_text()):
        name, value = match.groups()
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("Invalid quoted " + name + " in environment file")
            quote, value = value[0], value[1:-1]
            value = re.sub(r"\\([\\'])", lambda m: m[1], value) if quote == "'" else re.sub(
                r'''\\([\\'"abfnrtv])''', lambda m: escapes[m[1]], value,
            )
        else:
            value = re.sub(r"\s+#.*$", "", value).rstrip()
        settings[name] = value
    return settings


def option_value(value: str) -> str:
    """Quote a client option-file value without allowing extra options/lines."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace(
        "\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + '"'


def run_sql(command: list[str], statement: str, password: str) -> str:
    try:
        return subprocess.run(
            command, input=statement, text=True, capture_output=True, check=True, timeout=120,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "MariaDB client returned no diagnostic").strip()
        if password:
            detail = detail.replace(password, "[redacted]")
        raise RuntimeError("MariaDB client failed: " + detail[:1000]) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("MariaDB client timed out after 120 seconds") from None


def default_pool_size(proc: Path = Path("/proc"), cgroup: Path = Path("/sys/fs/cgroup")) -> int:
    memory = re.search(r"^MemTotal:\s+(\d+)\s+kB", (proc / "meminfo").read_text(), re.M)
    if not memory:
        raise RuntimeError("Cannot determine available memory for MariaDB tuning")
    limits = [int(memory[1]) * 1024]
    paths = {cgroup / "memory.max", cgroup / "memory/memory.limit_in_bytes"}
    # Include the current cgroup and its parents when the process is nested
    # below the namespace's root (for example in a limited systemd scope).
    for line in (proc / "self/cgroup").read_text().splitlines():
        _, controllers, relative = line.split(":", 2)
        if controllers == "" or "memory" in controllers.split(","):
            root = cgroup if controllers == "" else cgroup / "memory"
            current = root / relative.lstrip("/")
            while current == root or root in current.parents:
                paths.add(current / ("memory.max" if controllers == "" else "memory.limit_in_bytes"))
                if current == root:
                    break
                current = current.parent
    for path in paths:
        if path.is_file():
            value = path.read_text().strip()
            if value.isdigit() and int(value) > 0:
                limits.append(int(value))
    # Leave most RAM for MyPortal, MariaDB's other allocations and the OS.
    return max(16 * MIB, min(4 * GIB, min(limits) // 4 // (128 * MIB) * (128 * MIB)))


def size_bytes(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+)\s*([KMGT]?)\s*", value, re.I)
    if not match:
        raise ValueError("Unsupported innodb_buffer_pool_size value: " + value)
    return int(match[1]) * 1024 ** ("KMGT".index(match[2].upper()) + 1 if match[2] else 0)


def pool_settings(text: str) -> list[tuple[int, int]]:
    group = ""
    found = []
    for index, line in enumerate(text.splitlines()):
        if match := re.match(r"^\s*\[([^]]+)\]", line):
            group = match[1].lower()
        if SERVER_GROUP.fullmatch(group) and (match := POOL_SETTING.match(line)):
            found.append((index, size_bytes(match[1])))
    return found


def updated_config(text: str, target: int) -> str:
    settings = pool_settings(text)
    if len(settings) == 1 and settings[0][1] == target:
        preceding = text.splitlines()[:settings[0][0]]
        groups = [match[1] for line in preceding if (match := re.match(r"^\s*\[([^]]+)\]", line))]
        if groups and groups[-1].lower() in {"server", "mysqld", "mariadb", "mariadbd"}:
            return text
    indexes = {index for index, _ in settings}
    lines = [line for index, line in enumerate(text.splitlines()) if index not in indexes]
    value = str(target // MIB) + "M" if target % MIB == 0 else str(target)
    return "\n".join(lines).rstrip() + "\n\n[mysqld]\ninnodb_buffer_pool_size = " + value + "\n"


def write_config(path: Path, text: str) -> None:
    if path.is_symlink():
        raise RuntimeError("Refusing to replace symlinked MariaDB configuration")
    previous = path.stat() if path.exists() else None
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        staged = Path(handle.name)
        try:
            handle.write(text)
            handle.flush()
            os.fchmod(handle.fileno(), stat.S_IMODE(previous.st_mode) if previous else 0o644)
            if previous:
                os.fchown(handle.fileno(), previous.st_uid, previous.st_gid)
            os.fsync(handle.fileno())
            os.replace(staged, path)
        finally:
            staged.unlink(missing_ok=True)


def configure(env_file: Path, config: Path) -> None:
    settings = read_database_settings(env_file)
    if settings.get("DB_HOST", "").lower() not in {"localhost", "127.0.0.1", "::1"}:
        print("Skipping MariaDB buffer pool tuning: DB_HOST is not local.")
        return
    client = shutil.which("mariadb")
    if not client:
        raise RuntimeError("The local MariaDB client is required for buffer pool tuning")

    # Prefer the distro's root socket login, then use the same configured TCP
    # account as the application on hosts where root requires a password.
    # Credentials never appear in argv or logs; the private file is removed
    # even if probing or resizing fails.
    with tempfile.TemporaryDirectory(prefix="myportal-mariadb-") as private:
        commands = [[client, "--protocol=socket", "--user=root", "--batch", "--skip-column-names"]]
        password = settings.get("DB_PASSWORD", "")
        if settings.get("DB_USER"):
            options = Path(private) / "client.cnf"
            descriptor = os.open(options, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                handle.write("[client]\nprotocol=tcp\nhost=" + option_value(settings["DB_HOST"]) +
                             "\nport=" + str(int(settings.get("DB_PORT") or "3306")) +
                             "\nuser=" + option_value(settings["DB_USER"]) +
                             "\npassword=" + option_value(password) + "\n")
            commands.append([client, "--defaults-file=" + str(options), "--batch", "--skip-column-names"])
        errors = []
        for command in commands:
            try:
                result = run_sql(command, "SELECT @@version, @@port, @@innodb_buffer_pool_size;\n", password)
                break
            except RuntimeError as exc:
                errors.append(str(exc))
        else:
            raise RuntimeError("; ".join(errors))

        def sql(statement: str) -> str:
            return run_sql(command, statement, password)

        apply_configuration(config, settings, result, sql)


def apply_configuration(config: Path, settings: dict[str, str], result: str, sql: Callable[[str], str]) -> None:

    version, port, running = result.split("\t")
    if "mariadb" not in version.lower():
        raise RuntimeError("Buffer pool tuning requires a local MariaDB server")
    if int(port) != int(settings.get("DB_PORT") or "3306"):
        raise RuntimeError("The local MariaDB socket does not match the configured DB_PORT")
    original = config.read_text() if config.exists() else ""
    configured = [value for _, value in pool_settings(original)]
    target = max(default_pool_size(), int(running), *configured)
    if int(running) < target:
        # Only an integer computed above is added to this fixed SQL statement.
        sql("SET GLOBAL innodb_buffer_pool_size = " + str(int(target)) + ";\n")
        applied = int(sql("SELECT @@innodb_buffer_pool_size;\n"))
        if applied < target:
            raise RuntimeError("MariaDB did not apply the requested buffer pool size")
        target = applied
    updated = updated_config(original, target)
    if updated != original:
        write_config(config, updated)
    print("MariaDB innodb_buffer_pool_size: " + str(target // MIB) + " MiB (live and persistent).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env_file", type=Path)
    parser.add_argument("--config", type=Path, default=Path("/etc/mysql/mariadb.conf.d/50-server.cnf"))
    args = parser.parse_args()
    try:
        configure(args.env_file, args.config)
    except (OSError, RuntimeError, ValueError) as exc:
        print("MariaDB buffer pool tuning was not applied: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
