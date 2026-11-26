#!/usr/bin/env python3
"""Write a read-only snapshot of MyPortal's Fail2ban jails for the web UI.

Runs as root from ``myportal-fail2ban-snapshot.timer`` because Fail2ban's
control socket is root-only. The web service never talks to Fail2ban: it
only reads the JSON file this script writes, so the admin page is view only.

Standard library only; run with ``python3 -I``.
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import re
import shutil
import stat
import subprocess  # nosec B404 - fixed fail2ban-client argv, no shell
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Callable

SNAPSHOT_VERSION = 1
DEFAULT_OUTPUT = "/var/lib/myportal-fail2ban/status.json"
DEFAULT_GROUP = "myportal"
DEFAULT_JAIL_PREFIX = "myportal"
MAX_BANS_PER_JAIL = 5000
COMMAND_TIMEOUT = 10

Runner = Callable[[list[str]], str]

_JAIL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_WITH_TIME = re.compile(
    r"^(?P<ip>\S+)\s+(?P<start>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\+\s*(?P<seconds>-?\d+)"
    r"\s*=\s*(?P<end>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
)


class Fail2banError(RuntimeError):
    """fail2ban-client failed or is not available."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _status_fields(output: str) -> dict[str, str]:
    """Return ``key: value`` pairs from fail2ban-client's tree output."""

    fields: dict[str, str] = {}
    for line in output.splitlines():
        cleaned = line.lstrip(" |`-")
        if ":" not in cleaned:
            continue
        key, _, value = cleaned.partition(":")
        fields[key.strip().lower()] = value.strip()
    return fields


def parse_jail_list(output: str) -> list[str]:
    raw = _status_fields(output).get("jail list", "")
    names = [name.strip() for name in raw.split(",")]
    return [name for name in names if name and _JAIL_NAME.match(name)]


def _int(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_jail_status(output: str) -> dict[str, Any]:
    fields = _status_fields(output)
    files = [item.strip() for item in fields.get("file list", "").split() if item.strip()]
    banned = [item.strip() for item in fields.get("banned ip list", "").split() if item.strip()]
    return {
        "currently_failed": _int(fields.get("currently failed")),
        "total_failed": _int(fields.get("total failed")),
        "currently_banned": _int(fields.get("currently banned")),
        "total_banned": _int(fields.get("total banned")),
        "files": files,
        "banned_ips": banned,
    }


def _local_to_utc(value: str) -> str | None:
    """Convert fail2ban's server-local ``YYYY-MM-DD HH:MM:SS`` to UTC ISO 8601."""

    try:
        parsed = time.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    epoch = time.mktime(parsed)
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def parse_bans_with_time(output: str) -> list[dict[str, Any]]:
    bans: list[dict[str, Any]] = []
    for line in output.splitlines():
        match = _WITH_TIME.match(line.strip())
        if not match:
            continue
        seconds = int(match.group("seconds"))
        bans.append(
            {
                "ip": match.group("ip"),
                "banned_at": _local_to_utc(match.group("start")),
                "ban_seconds": seconds,
                # A negative ban time means a permanent ban.
                "unbans_at": None if seconds < 0 else _local_to_utc(match.group("end")),
            }
        )
    return bans


def _parse_bool(value: str) -> bool | None:
    lowered = value.strip().lower()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    return None


def _jail_settings(run: Runner, jail: str) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    for key, name, parse in (
        ("bantime", "bantime", _int),
        ("findtime", "findtime", _int),
        ("maxretry", "maxretry", _int),
        ("bantime.increment", "bantime_increment", _parse_bool),
        ("bantime.maxtime", "bantime_maxtime", _int),
    ):
        try:
            settings[name] = parse(run(["get", jail, key]))
        except Fail2banError:
            settings[name] = None
    return settings


def collect_jail(run: Runner, jail: str, *, max_bans: int = MAX_BANS_PER_JAIL) -> dict[str, Any]:
    status = parse_jail_status(run(["status", jail]))
    banned_ips = status.pop("banned_ips")
    try:
        bans = parse_bans_with_time(run(["get", jail, "banip", "--with-time"]))
    except Fail2banError:
        bans = []
    if not bans and banned_ips:
        # Fail2ban 0.10 has no --with-time: keep the addresses without times.
        bans = [{"ip": ip, "banned_at": None, "ban_seconds": None, "unbans_at": None} for ip in banned_ips]
    return {
        "name": jail,
        "running": True,
        **status,
        "settings": _jail_settings(run, jail),
        "bans": bans[:max_bans],
        "bans_truncated": len(bans) > max_bans,
    }


def build_snapshot(run: Runner, *, prefix: str = DEFAULT_JAIL_PREFIX) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "version": SNAPSHOT_VERSION,
        "generated_at": _utc_now(),
        "fail2ban": {"available": True, "running": True, "error": None, "version": None},
        "jails": [],
    }
    try:
        snapshot["fail2ban"]["version"] = run(["version"]).strip() or None
        jails = parse_jail_list(run(["status"]))
    except Fail2banError as exc:
        snapshot["fail2ban"].update(running=False, error=str(exc))
        return snapshot
    for jail in jails:
        if not jail.startswith(prefix):
            continue
        try:
            snapshot["jails"].append(collect_jail(run, jail))
        except Fail2banError as exc:
            snapshot["jails"].append({"name": jail, "running": False, "error": str(exc), "bans": []})
    return snapshot


def make_runner(client: str) -> Runner:
    def run(args: list[str]) -> str:
        try:
            completed = subprocess.run(  # nosec B603 - absolute client path, validated jail names
                [client, *args],
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Fail2banError(f"fail2ban-client {' '.join(args)} failed: {exc}") from exc
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "").strip().splitlines()
            detail = message[-1] if message else f"exit code {completed.returncode}"
            raise Fail2banError(f"fail2ban-client {' '.join(args)} failed: {detail}")
        return completed.stdout

    return run


def _prepare_directory(directory: str, gid: int) -> None:
    """Create or check the output directory: a real, root-owned directory."""

    try:
        os.mkdir(directory, 0o750)
    except FileExistsError:
        pass
    info = os.lstat(directory)
    if not stat.S_ISDIR(info.st_mode):
        raise SystemExit(f"Refusing to write: {directory} is not a directory.")
    if info.st_uid != os.geteuid():
        raise SystemExit(f"Refusing to write: {directory} is not owned by this user.")
    os.chown(directory, os.geteuid(), gid)
    os.chmod(directory, 0o750)  # nosec B103 - group myportal must read the snapshot


def write_snapshot(snapshot: dict[str, Any], output: str, gid: int) -> None:
    directory = os.path.dirname(os.path.abspath(output))
    _prepare_directory(directory, gid)
    fd, temp_path = tempfile.mkstemp(prefix=".status-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o640)
            os.fchown(handle.fileno(), os.geteuid(), gid)
            json.dump(snapshot, handle, indent=1, sort_keys=True)
            handle.write("\n")
        os.replace(temp_path, output)
    except BaseException:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Snapshot file to write.")
    parser.add_argument("--group", default=DEFAULT_GROUP, help="Group that may read the snapshot.")
    parser.add_argument("--jail-prefix", default=DEFAULT_JAIL_PREFIX, help="Only report jails starting with this.")
    args = parser.parse_args(argv)

    try:
        gid = grp.getgrnam(args.group).gr_gid
    except KeyError:
        print(f"Group {args.group!r} does not exist.", file=sys.stderr)
        return 1

    client = shutil.which("fail2ban-client", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if client is None:
        snapshot = {
            "version": SNAPSHOT_VERSION,
            "generated_at": _utc_now(),
            "fail2ban": {"available": False, "running": False, "error": "fail2ban-client is not installed.", "version": None},
            "jails": [],
        }
    else:
        snapshot = build_snapshot(make_runner(client), prefix=args.jail_prefix)
    write_snapshot(snapshot, args.output, gid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
