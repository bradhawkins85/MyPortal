"""Tests for scripts/fail2ban_snapshot.py, the root job behind /admin/fail2ban."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fail2ban_snapshot.py"
_spec = importlib.util.spec_from_file_location("fail2ban_snapshot", SCRIPT)
snapshot_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snapshot_script)

STATUS = """Status
|- Number of jail:\t3
`- Jail list:\tmyportal-auth, myportal-honeypot, sshd
"""

AUTH_STATUS = """Status for the jail: myportal-auth
|- Filter
|  |- Currently failed:\t4
|  |- Total failed:\t37
|  `- File list:\t/var/log/myportal/auth.log
`- Actions
   |- Currently banned:\t2
   |- Total banned:\t9
   `- Banned IP list:\t203.0.113.10 2001:db8::5
"""

AUTH_BANS = (
    "203.0.113.10 \t2026-11-24 10:04:00 + 3600 = 2026-11-24 11:04:00\n"
    "2001:db8::5 \t2026-11-24 10:30:00 + -1 = 9999-12-31 23:59:59\n"
)

HONEYPOT_STATUS = AUTH_STATUS.replace("myportal-auth", "myportal-honeypot")


def fake_runner(responses: dict[tuple[str, ...], str]):
    def run(args: list[str]) -> str:
        key = tuple(args)
        if key not in responses:
            raise snapshot_script.Fail2banError(f"unexpected {' '.join(args)}")
        return responses[key]

    return run


@pytest.fixture(autouse=True)
def utc_local_time(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    import time

    time.tzset()


def test_parse_jail_list_keeps_only_safe_names():
    assert snapshot_script.parse_jail_list(STATUS) == ["myportal-auth", "myportal-honeypot", "sshd"]
    assert snapshot_script.parse_jail_list("`- Jail list:\tgood, bad name;rm") == ["good"]


def test_parse_jail_status_counts_and_lists():
    parsed = snapshot_script.parse_jail_status(AUTH_STATUS)
    assert parsed == {
        "currently_failed": 4,
        "total_failed": 37,
        "currently_banned": 2,
        "total_banned": 9,
        "files": ["/var/log/myportal/auth.log"],
        "banned_ips": ["203.0.113.10", "2001:db8::5"],
    }


def test_parse_bans_with_time_converts_to_utc_and_handles_permanent_bans():
    bans = snapshot_script.parse_bans_with_time(AUTH_BANS)
    assert bans == [
        {
            "ip": "203.0.113.10",
            "banned_at": "2026-11-24T10:04:00+00:00",
            "ban_seconds": 3600,
            "unbans_at": "2026-11-24T11:04:00+00:00",
        },
        {
            "ip": "2001:db8::5",
            "banned_at": "2026-11-24T10:30:00+00:00",
            "ban_seconds": -1,
            "unbans_at": None,
        },
    ]


def _responses(overrides=None):
    responses = {
        ("version",): "1.0.2\n",
        ("status",): STATUS,
        ("status", "myportal-auth"): AUTH_STATUS,
        ("get", "myportal-auth", "banip", "--with-time"): AUTH_BANS,
        ("get", "myportal-auth", "bantime"): "3600\n",
        ("get", "myportal-auth", "findtime"): "300\n",
        ("get", "myportal-auth", "maxretry"): "5\n",
        ("get", "myportal-auth", "bantime.increment"): "False\n",
        ("get", "myportal-auth", "bantime.maxtime"): "-1\n",
        ("status", "myportal-honeypot"): HONEYPOT_STATUS,
    }
    responses.update(overrides or {})
    return responses


def test_build_snapshot_reports_only_myportal_jails():
    snapshot = snapshot_script.build_snapshot(fake_runner(_responses()))

    assert snapshot["fail2ban"] == {"available": True, "running": True, "error": None, "version": "1.0.2"}
    assert [jail["name"] for jail in snapshot["jails"]] == ["myportal-auth", "myportal-honeypot"]
    auth = snapshot["jails"][0]
    assert auth["settings"] == {
        "bantime": 3600, "findtime": 300, "maxretry": 5,
        "bantime_increment": False, "bantime_maxtime": -1,
    }
    assert [ban["ip"] for ban in auth["bans"]] == ["203.0.113.10", "2001:db8::5"]
    assert auth["bans_truncated"] is False


def test_build_snapshot_falls_back_to_status_ban_list_without_times():
    """Fail2ban 0.10 has no ``banip --with-time``; the honeypot jail here has none."""
    snapshot = snapshot_script.build_snapshot(fake_runner(_responses()))
    honeypot = snapshot["jails"][1]
    assert honeypot["bans"] == [
        {"ip": "203.0.113.10", "banned_at": None, "ban_seconds": None, "unbans_at": None},
        {"ip": "2001:db8::5", "banned_at": None, "ban_seconds": None, "unbans_at": None},
    ]
    assert honeypot["settings"]["bantime"] is None


def test_build_snapshot_records_a_stopped_server():
    def run(args):
        raise snapshot_script.Fail2banError("fail2ban-client status failed: Failed to access socket path")

    snapshot = snapshot_script.build_snapshot(run)
    assert snapshot["fail2ban"]["running"] is False
    assert "socket" in snapshot["fail2ban"]["error"]
    assert snapshot["jails"] == []


def test_collect_jail_caps_the_ban_list():
    many = "".join(
        f"198.51.100.{i % 250} \t2026-11-24 10:00:00 + 60 = 2026-11-24 10:01:00\n" for i in range(12)
    )
    responses = _responses({("get", "myportal-auth", "banip", "--with-time"): many})
    jail = snapshot_script.collect_jail(fake_runner(responses), "myportal-auth", max_bans=5)
    assert len(jail["bans"]) == 5
    assert jail["bans_truncated"] is True


def test_write_snapshot_is_group_readable_and_atomic(tmp_path):
    output = tmp_path / "state" / "status.json"
    gid = os.getegid()

    snapshot_script.write_snapshot({"version": 1, "jails": []}, str(output), gid)

    assert json.loads(output.read_text()) == {"version": 1, "jails": []}
    assert stat.S_IMODE(output.stat().st_mode) == 0o640
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o750
    assert [p.name for p in output.parent.iterdir()] == ["status.json"]


def test_write_snapshot_refuses_a_symlinked_directory(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "state"
    link.symlink_to(real)

    with pytest.raises(SystemExit, match="not a directory"):
        snapshot_script.write_snapshot({"version": 1}, str(link / "status.json"), os.getegid())
    assert list(real.iterdir()) == []
