"""Read-only Fail2ban status for the ``/admin/fail2ban`` page.

Two sources, neither of which lets the web service change Fail2ban:

* the JSON snapshot written every minute by the root
  ``myportal-fail2ban-snapshot.timer`` (``scripts/fail2ban_snapshot.py``):
  jails, counts, settings and the current ban list;
* the end of ``FAIL2BAN_LOG_PATH``: the events MyPortal reported.

Everything here is blocking file I/O; call :func:`build_overview` from a
worker thread.
"""

from __future__ import annotations

import gzip
import ipaddress
import json
import os
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

SNAPSHOT_VERSION = 1
SNAPSHOT_STALE_AFTER = timedelta(minutes=5)
ACTIVITY_WINDOW = timedelta(hours=24)
MAX_TAIL_BYTES = 4 * 1024 * 1024
LATEST_EVENTS = 50
TOP_ADDRESSES = 10
PROXY_CHECK_MIN_EVENTS = 10

EVENT_LOGIN_FAIL = "login_fail"
EVENT_LOGIN_SUCCESS = "login_success"
EVENT_TRAP = "trap"
EVENT_PROBE = "probe"

_EVENT_TAGS = (
    ("AUTH LOGIN FAIL", EVENT_LOGIN_FAIL),
    ("AUTH LOGIN SUCCESS", EVENT_LOGIN_SUCCESS),
    ("HONEYPOT TRAP", EVENT_TRAP),
    ("HONEYPOT PROBE", EVENT_PROBE),
)

EVENT_LABELS = {
    EVENT_LOGIN_FAIL: "Failed sign-in",
    EVENT_LOGIN_SUCCESS: "Signed in",
    EVENT_TRAP: "Scanner trap",
    EVENT_PROBE: "Unknown page",
}

EVENT_JAILS = {
    EVENT_LOGIN_FAIL: "myportal-auth",
    EVENT_TRAP: "myportal-honeypot",
    EVENT_PROBE: "myportal-probe",
}


@dataclass(frozen=True)
class JailInfo:
    label: str
    description: str
    noun: str


EXPECTED_JAILS: dict[str, JailInfo] = {
    "myportal-auth": JailInfo(
        "Sign-in failures", "Blocks addresses that keep entering wrong passwords.", "failed sign-ins"
    ),
    "myportal-honeypot": JailInfo(
        "Scanner traps",
        "Blocks bots that ask for files a portal never serves, such as /.env or /wp-login.php.",
        "scanner requests",
    ),
    "myportal-probe": JailInfo(
        "Unknown pages",
        "Blocks anonymous visitors that keep asking for pages and API endpoints that don't exist.",
        "unknown-page requests",
    ),
}


@dataclass
class LogEvent:
    occurred_at: datetime
    kind: str
    ip: str
    fields: dict[str, str]

    @property
    def label(self) -> str:
        return EVENT_LABELS[self.kind]

    @property
    def jail(self) -> str | None:
        return EVENT_JAILS.get(self.kind)

    @property
    def detail(self) -> str:
        if self.kind in (EVENT_TRAP, EVENT_PROBE):
            method = self.fields.get("method", "")
            path = self.fields.get("path", "")
            return f"{method} {path}".strip()
        if self.kind == EVENT_LOGIN_FAIL:
            return self.fields.get("reason", "").replace("_", " ")
        if self.kind == EVENT_LOGIN_SUCCESS:
            return self.fields.get("auth_method", "")
        return ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "occurred_at": self.occurred_at.isoformat(),
            "kind": self.kind,
            "label": self.label,
            "ip": self.ip,
            "detail": self.detail,
        }


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _first_fields(text: str) -> dict[str, str]:
    """Return ``key=value`` tokens, keeping the first occurrence of each key.

    The Fail2ban filters take the address from the first ``ip=`` field;
    later fields (paths, user agents) are attacker-controlled and must not
    replace it.
    """

    fields: dict[str, str] = {}
    for token in text.split():
        key, sep, value = token.partition("=")
        if sep and key.replace("_", "").isalnum():
            fields.setdefault(key, value)
    return fields


def parse_log_line(line: str) -> LogEvent | None:
    parts = line.rstrip("\n").split(" | ", 5)
    if len(parts) < 6:
        return None
    occurred_at = _parse_time(parts[0].strip())
    if occurred_at is None:
        return None
    message = parts[5]
    for tag, kind in _EVENT_TAGS:
        if message.startswith(tag):
            rest = message[len(tag):].lstrip(" |")
            fields = _first_fields(rest)
            ip = fields.get("ip", "")
            if not ip:
                return None
            return LogEvent(occurred_at, kind, ip, fields)
    return None


def _tail_lines(path: Path, max_bytes: int = MAX_TAIL_BYTES) -> list[str]:
    if path.suffix == ".gz":
        chunks: deque[bytes] = deque()
        size = 0
        with gzip.open(path, "rb") as handle:
            while chunk := handle.read(256 * 1024):
                chunks.append(chunk)
                size += len(chunk)
                while size - len(chunks[0]) >= max_bytes:
                    size -= len(chunks.popleft())
        data = b"".join(chunks)[-max_bytes:]
        partial = size > max_bytes
    else:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            end = handle.tell()
            start = max(0, end - max_bytes)
            handle.seek(start)
            data = handle.read()
        partial = start > 0
    lines = data.decode("utf-8", errors="replace").splitlines()
    if partial and lines:
        lines = lines[1:]
    return lines


def _previous_log(path: Path) -> Path | None:
    """Return the newest rotated sibling of ``path`` (loguru naming)."""

    pattern = f"{path.stem}.*{path.suffix}*"
    candidates = [p for p in path.parent.glob(pattern) if p != path and p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


@dataclass
class LogRead:
    configured: bool
    path: str | None
    exists: bool = False
    readable: bool = True
    error: str | None = None
    events: list[LogEvent] | None = None
    last_event_at: datetime | None = None
    # Newest line of any kind. The Fail2ban sink also receives MyPortal's
    # ordinary INFO messages, so this shows the log is being written even
    # when nobody has failed to sign in.
    last_entry_at: datetime | None = None


def _last_entry_time(lines: list[str]) -> datetime | None:
    for raw in reversed(lines):
        stamp, sep, _ = raw.partition(" | ")
        if sep:
            parsed = _parse_time(stamp.strip())
            if parsed is not None:
                return parsed
    return None


def read_recent_events(path: Path | None, now: datetime, window: timedelta = ACTIVITY_WINDOW) -> LogRead:
    if path is None:
        return LogRead(configured=False, path=None, events=[])
    path = path.expanduser()
    result = LogRead(configured=True, path=str(path), events=[])
    if not path.exists():
        return result
    result.exists = True
    since = now - window
    try:
        lines = _tail_lines(path)
        result.last_entry_at = _last_entry_time(lines)
        events = [event for event in map(parse_log_line, lines) if event]
        if not events or events[0].occurred_at > since:
            previous = _previous_log(path)
            if previous is not None:
                older_lines = _tail_lines(previous)
                if result.last_entry_at is None:
                    result.last_entry_at = _last_entry_time(older_lines)
                older = [event for event in map(parse_log_line, older_lines) if event]
                events = older + events
    except OSError as exc:
        result.readable = False
        result.error = str(exc)
        return result
    if events:
        result.last_event_at = max(event.occurred_at for event in events)
    result.events = [event for event in events if since <= event.occurred_at <= now + timedelta(minutes=5)]
    return result


@dataclass
class SnapshotRead:
    configured: bool
    path: str | None
    state: str  # ok, missing, unreadable, invalid, disabled
    data: dict[str, Any] | None = None
    error: str | None = None
    generated_at: datetime | None = None
    age_seconds: int | None = None
    stale: bool = False


def load_snapshot(path: Path | None, now: datetime) -> SnapshotRead:
    if path is None:
        return SnapshotRead(configured=False, path=None, state="disabled")
    path = path.expanduser()
    result = SnapshotRead(configured=True, path=str(path), state="ok")
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        result.state = "missing"
        return result
    except OSError as exc:
        result.state = "unreadable"
        result.error = str(exc)
        return result
    try:
        data = json.loads(raw)
    except ValueError as exc:
        result.state = "invalid"
        result.error = str(exc)
        return result
    if not isinstance(data, dict) or data.get("version") != SNAPSHOT_VERSION:
        result.state = "invalid"
        result.error = "Unsupported snapshot format."
        return result
    result.data = data
    generated_at = _parse_time(str(data.get("generated_at") or ""))
    result.generated_at = generated_at
    if generated_at is not None:
        result.age_seconds = max(0, int((now - generated_at).total_seconds()))
        result.stale = now - generated_at > SNAPSHOT_STALE_AFTER
    else:
        result.stale = True
    return result


def humanize_seconds(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    if seconds < 0:
        return "permanently"
    for size, unit in ((604800, "week"), (86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size and seconds % size == 0:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    if seconds >= 3600:
        hours = seconds / 3600
        return f"{hours:.1f} hours"
    if seconds >= 60:
        return f"{seconds // 60} minutes"
    return f"{seconds} seconds"


def describe_rules(info: JailInfo | None, settings: dict[str, Any]) -> str | None:
    maxretry = settings.get("maxretry")
    findtime = humanize_seconds(settings.get("findtime"))
    bantime = settings.get("bantime")
    if maxretry is None or bantime is None:
        return None
    noun = info.noun if info else "matches"
    if maxretry <= 1:
        trigger = f"on the first of its {noun}"
    else:
        trigger = f"after {maxretry} {noun}"
        if findtime:
            trigger += f" within {findtime}"
    ban = humanize_seconds(bantime)
    text = f"Blocks an address {trigger}, {'for ' + ban if bantime >= 0 else ban}."
    if settings.get("bantime_increment"):
        maxtime = humanize_seconds(settings.get("bantime_maxtime"))
        text += " Repeat offenders are blocked for longer each time"
        text += f", up to {maxtime}." if maxtime and settings.get("bantime_maxtime", -1) > 0 else "."
    return text


def humanize_age(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "less than a minute"
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return f"{seconds} seconds"  # pragma: no cover - unreachable


def _plural(count: int, word: str) -> str:
    if count == 1:
        return f"{count} {word}"
    return f"{count} {word}{'es' if word.endswith('s') else 's'}"


def _is_loopback(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _same_file(left: str, right: str) -> bool:
    try:
        return os.path.realpath(left) == os.path.realpath(right)
    except (OSError, ValueError):
        return left == right


def _check(status: str, title: str, detail: str) -> dict[str, str]:
    return {"status": status, "title": title, "detail": detail}


def build_health(
    log: LogRead, snapshot: SnapshotRead, jails: list[dict[str, Any]], now: datetime | None = None
) -> list[dict[str, str]]:
    now = now or datetime.now(timezone.utc)
    checks: list[dict[str, str]] = []
    events = log.events or []

    if not log.configured:
        checks.append(_check(
            "danger", "MyPortal isn't writing a Fail2ban log",
            "Set FAIL2BAN_LOG_PATH in MyPortal's .env (for example /var/log/myportal/auth.log) and restart MyPortal.",
        ))
    elif not log.exists:
        checks.append(_check(
            "danger", "The Fail2ban log file doesn't exist yet",
            f"MyPortal should create {log.path} when it starts. Check the service journal for "
            "'LOG FILE DISABLED' or 'IGNORED' warnings.",
        ))
    elif not log.readable:
        checks.append(_check("danger", "The Fail2ban log file can't be read", f"{log.path}: {log.error}"))
    elif log.last_entry_at is None:
        checks.append(_check(
            "warning", "The Fail2ban log hasn't received any entries",
            f"{log.path} is empty. Check the service journal for 'LOG FILE DISABLED' or 'IGNORED' warnings "
            "and restart MyPortal.",
        ))
    elif now - log.last_entry_at > ACTIVITY_WINDOW:
        checks.append(_check(
            "warning", f"The Fail2ban log has had no new entries for {humanize_age(now - log.last_entry_at)}",
            f"MyPortal normally writes to {log.path} every day. Check the service journal for "
            "'LOG FILE DISABLED' or 'IGNORED' warnings and restart MyPortal.",
        ))
    else:
        checks.append(_check(
            "success", "The Fail2ban log is receiving entries",
            f"Last entry {humanize_age(now - log.last_entry_at)} ago in {log.path}.",
        ))
        failures = [event for event in events if event.kind == EVENT_LOGIN_FAIL]
        if failures:
            addresses = len({event.ip for event in failures})
            checks.append(_check(
                "info", f"{_plural(len(failures), 'failed sign-in')} in the last 24 hours",
                f"From {_plural(addresses, 'address')}. The Sign-in failures jail blocks repeat offenders.",
            ))
        else:
            checks.append(_check(
                "info", "No failed sign-ins in the last 24 hours",
                "Nobody has entered a wrong password. Failed sign-ins appear here as soon as they happen.",
            ))

    blocking = [event for event in events if event.kind != EVENT_LOGIN_SUCCESS]
    if len(blocking) >= PROXY_CHECK_MIN_EVENTS:
        loopback = sum(1 for event in blocking if _is_loopback(event.ip))
        if loopback * 2 > len(blocking):
            share = round(100 * loopback / len(blocking))
            checks.append(_check(
                "danger", f"{share}% of events come from the server itself",
                "MyPortal is recording your reverse proxy's address instead of the visitor's, and the jails never "
                "block it. Set TRUSTED_PROXIES=127.0.0.1,::1 in .env and restart MyPortal.",
            ))

    if snapshot.state == "disabled":
        checks.append(_check(
            "off", "The ban list is turned off",
            "Set FAIL2BAN_STATUS_PATH to show who Fail2ban has blocked.",
        ))
        return checks
    if snapshot.state == "missing":
        checks.append(_check(
            "danger", "No ban snapshot yet",
            "The job that reads Fail2ban hasn't run. On the server run: "
            "sudo systemctl enable --now myportal-fail2ban-snapshot.timer",
        ))
        return checks
    if snapshot.state == "unreadable":
        checks.append(_check(
            "danger", "MyPortal can't read the ban snapshot",
            f"{snapshot.path}: {snapshot.error}. The file should be owned by root, group myportal, mode 640. "
            "Run: sudo systemctl start myportal-fail2ban-snapshot.service",
        ))
        return checks
    if snapshot.state == "invalid":
        checks.append(_check("danger", "The ban snapshot couldn't be read", f"{snapshot.path}: {snapshot.error}"))
        return checks

    if snapshot.stale:
        checks.append(_check(
            "warning", "The ban snapshot is out of date",
            "It should refresh every minute. Check it with: sudo systemctl status myportal-fail2ban-snapshot.timer",
        ))
    else:
        checks.append(_check(
            "success", "The ban snapshot is current",
            f"Fail2ban was checked {snapshot.age_seconds} seconds ago.",
        ))

    fail2ban = (snapshot.data or {}).get("fail2ban") or {}
    if not fail2ban.get("available"):
        checks.append(_check(
            "danger", "Fail2ban isn't installed",
            "Install it (sudo apt install fail2ban) and copy the MyPortal filters and jails from deploy/fail2ban/.",
        ))
        return checks
    if not fail2ban.get("running"):
        checks.append(_check(
            "danger", "Fail2ban isn't running",
            f"{fail2ban.get('error') or 'The Fail2ban server did not answer.'} Start it with: sudo systemctl start fail2ban",
        ))
        return checks

    for jail in jails:
        if not jail["loaded"]:
            checks.append(_check(
                "warning", f"The {jail['label']} jail isn't loaded",
                f"Copy the {jail['name']} filter and jail from deploy/fail2ban/ into /etc/fail2ban/ and restart Fail2ban.",
            ))
        elif not jail["running"]:
            checks.append(_check("danger", f"The {jail['label']} jail has a problem", jail.get("error") or ""))
        elif log.path and jail["files"] and not any(_same_file(f, log.path) for f in jail["files"]):
            checks.append(_check(
                "warning", f"The {jail['label']} jail reads a different file",
                f"It watches {', '.join(jail['files'])} but MyPortal writes {log.path}. Set logpath in its jail file.",
            ))
    return checks


def build_jails(snapshot: SnapshotRead) -> list[dict[str, Any]]:
    reported = {
        str(jail.get("name")): jail
        for jail in ((snapshot.data or {}).get("jails") or [])
        if isinstance(jail, dict) and jail.get("name")
    }
    names = list(EXPECTED_JAILS) + sorted(name for name in reported if name not in EXPECTED_JAILS)
    jails = []
    for name in names:
        data = reported.get(name)
        info = EXPECTED_JAILS.get(name)
        settings = (data or {}).get("settings") or {}
        jails.append({
            "name": name,
            "label": info.label if info else name,
            "description": info.description if info else "",
            "loaded": data is not None,
            "running": bool(data and data.get("running")),
            "error": (data or {}).get("error"),
            "currently_banned": (data or {}).get("currently_banned"),
            "total_banned": (data or {}).get("total_banned"),
            "currently_failed": (data or {}).get("currently_failed"),
            "total_failed": (data or {}).get("total_failed"),
            "files": [str(item) for item in ((data or {}).get("files") or [])],
            "rules": describe_rules(info, settings) if data else None,
            "bans_truncated": bool((data or {}).get("bans_truncated")),
        })
    return jails


def build_bans(snapshot: SnapshotRead, jails: list[dict[str, Any]], events: Iterable[LogEvent]) -> list[dict[str, Any]]:
    labels = {jail["name"]: jail["label"] for jail in jails}
    latest: dict[tuple[str, str], LogEvent] = {}
    latest_any: dict[str, LogEvent] = {}
    for event in events:
        if event.kind == EVENT_LOGIN_SUCCESS:
            continue
        latest_any[event.ip] = event
        if event.jail:
            latest[(event.jail, event.ip)] = event
    rows = []
    for jail in (snapshot.data or {}).get("jails") or []:
        if not isinstance(jail, dict):
            continue
        name = str(jail.get("name") or "")
        for ban in jail.get("bans") or []:
            if not isinstance(ban, dict) or not ban.get("ip"):
                continue
            ip = str(ban["ip"])
            reason = latest.get((name, ip)) or latest_any.get(ip)
            seconds = ban.get("ban_seconds")
            rows.append({
                "ip": ip,
                "jail": name,
                "jail_label": labels.get(name, name),
                "banned_at": ban.get("banned_at"),
                "unbans_at": ban.get("unbans_at"),
                "permanent": isinstance(seconds, int) and seconds < 0,
                "reason": f"{reason.label}: {reason.detail}".rstrip(": ") if reason else None,
            })
    rows.sort(key=lambda row: row["banned_at"] or "", reverse=True)
    return rows


def build_activity(events: list[LogEvent], banned_ips: set[str]) -> dict[str, Any]:
    counts = Counter(event.kind for event in events)
    per_ip: dict[str, Counter] = {}
    last_seen: dict[str, datetime] = {}
    for event in events:
        if event.kind == EVENT_LOGIN_SUCCESS:
            continue
        per_ip.setdefault(event.ip, Counter())[event.kind] += 1
        last_seen[event.ip] = event.occurred_at
    ranked = sorted(per_ip.items(), key=lambda item: (-sum(item[1].values()), item[0]))[:TOP_ADDRESSES]
    return {
        "counts": [
            {"kind": kind, "label": EVENT_LABELS[kind], "count": counts.get(kind, 0)}
            for kind in (EVENT_LOGIN_FAIL, EVENT_TRAP, EVENT_PROBE, EVENT_LOGIN_SUCCESS)
        ],
        "addresses": len(per_ip),
        "top_addresses": [
            {
                "ip": ip,
                "total": sum(kinds.values()),
                "breakdown": ", ".join(
                    f"{kinds[kind]} {EVENT_LABELS[kind].lower()}"
                    for kind in (EVENT_LOGIN_FAIL, EVENT_TRAP, EVENT_PROBE)
                    if kinds.get(kind)
                ),
                "last_seen": last_seen[ip].isoformat(),
                "blocked": ip in banned_ips,
            }
            for ip, kinds in ranked
        ],
        "latest": [event.as_dict() for event in reversed(events[-LATEST_EVENTS:])],
    }


def build_overview(log_path: Path | None, status_path: Path | None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    log = read_recent_events(log_path, now)
    snapshot = load_snapshot(status_path, now)
    jails = build_jails(snapshot) if snapshot.state == "ok" else []
    events = log.events or []
    bans = build_bans(snapshot, jails, events) if snapshot.state == "ok" else []
    fail2ban = (snapshot.data or {}).get("fail2ban") or {}
    return {
        "health": build_health(log, snapshot, jails, now),
        "snapshot": {
            "state": snapshot.state,
            "generated_at": snapshot.generated_at.isoformat() if snapshot.generated_at else None,
            "age_seconds": snapshot.age_seconds,
            "stale": snapshot.stale,
            "fail2ban_version": fail2ban.get("version"),
            "fail2ban_running": bool(fail2ban.get("running")),
        },
        "jails": jails,
        "bans": bans,
        "activity": build_activity(events, {row["ip"] for row in bans}),
        "log_path": log.path,
    }
