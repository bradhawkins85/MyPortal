"""Update-log line stamps must be real UTC whatever the server's time zone.

The System updates page reads each ``[...Z]`` stamp as UTC and shows it in the
viewer's local time. Stamping the server's local clock with a ``Z`` made the
log show times hours in the future on servers outside UTC.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
COORDINATORS = ["process_update_flag.sh", "myportal-docker.sh"]
STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z\] line$")
ZONE = Path("/usr/share/zoneinfo/Australia/Sydney")


def _timestamp_function(name: str) -> str:
    contents = (SCRIPTS / name).read_text(encoding="utf-8")
    return re.search(r"^timestamp_lines\(\) \{.*?^\}", contents, re.S | re.M).group(0)


@pytest.mark.parametrize("name", COORDINATORS)
def test_timestamp_lines_exports_a_fileless_utc_zone(name):
    function = _timestamp_function(name)
    assert "local -x TZ=UTC0" in function
    assert "local TZ=UTC " not in function


@pytest.mark.parametrize("name", COORDINATORS)
def test_timestamp_lines_is_utc_when_the_server_zone_is_not(name, tmp_path):
    """Run with no TZ variable and the system zone set to Sydney (UTC+10/+11)."""

    unshare = shutil.which("unshare")
    if not unshare or not ZONE.exists() or not Path("/etc/localtime").exists():
        pytest.skip("needs unshare and the tz database to emulate a non-UTC server")
    probe = subprocess.run(
        [unshare, "--user", "--map-root-user", "--mount", "true"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        # CI runners often forbid unprivileged user namespaces; the source
        # check above still guards the fix there.
        pytest.skip("user/mount namespaces unavailable: " + probe.stderr.strip())
    snippet = tmp_path / "stamp.sh"
    snippet.write_text(_timestamp_function(name) + "\nprintf 'line\\n' | timestamp_lines\n")
    program = (
        f'mount --bind "{ZONE}" "$(readlink -f /etc/localtime)" '
        f'&& exec env -u TZ bash "{snippet}"'
    )
    result = subprocess.run(
        [unshare, "--user", "--map-root-user", "--mount", "bash", "-c", program],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    match = STAMP.match(result.stdout.strip())
    assert match, result.stdout
    stamped = datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - stamped).total_seconds()) < 120
