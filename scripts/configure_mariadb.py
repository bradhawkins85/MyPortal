#!/usr/bin/env python3
"""Persist and apply a memory-aware buffer pool default for local MariaDB."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import tempfile


MIB = 1024 ** 2
GIB = 1024 ** 3
SERVER_GROUP = re.compile(r"^(?:server|mysqld|mariadb|mariadbd)(?:-[\d.]+)?$")
POOL_SETTING = re.compile(r"^\s*innodb[-_]buffer[-_]pool[-_]size\s*=\s*([^#;]+)", re.I)


def read_database_settings(path: Path) -> dict[str, str]:
    settings = {}
    for line in path.read_text().splitlines():
        name, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and name.strip() in {"DB_HOST", "DB_PORT"}:
            words = shlex.split(value, comments=True)
            settings[name.strip()] = words[0] if words else ""
    return settings


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

    def sql(statement: str) -> str:
        return subprocess.run(
            [client, "--protocol=socket", "--batch", "--skip-column-names"],
            input=statement, text=True, capture_output=True, check=True, timeout=120,
        ).stdout.strip()

    version, port, running = sql("SELECT @@version, @@port, @@innodb_buffer_pool_size;\n").split("\t")
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
    configure(args.env_file, args.config)


if __name__ == "__main__":
    main()
