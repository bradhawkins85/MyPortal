"""Exercise the shipped macOS script with mocked macOS tools and network.

Runs on any machine with bash, without installing RustDesk or touching launchd:
/Applications and /Library point into a temporary folder and curl, hdiutil,
ditto, launchctl and friends are stubs that log what they were asked to do.
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

BASH = shutil.which("bash")
SLEEP = shutil.which("sleep")
pytestmark = pytest.mark.skipif(BASH is None or SLEEP is None, reason="bash is required")
SCRIPT = Path(__file__).resolve().parents[1] / "app/services/rmm_builtin/rustdesk_activate_macos.sh"

RELEASE = (
    '{"tag_name":"9.8.7","assets":['
    '{"name":"rustdesk-9.8.7-x86_64.dmg","browser_download_url":"https://github.com/rustdesk/rustdesk/releases/download/9.8.7/rustdesk-9.8.7-x86_64.dmg"},'
    '{"name":"rustdesk-9.8.7-aarch64.dmg","browser_download_url":"https://github.com/rustdesk/rustdesk/releases/download/9.8.7/rustdesk-9.8.7-aarch64.dmg"},'
    '{"name":"rustdesk-9.8.7-x86_64.exe","browser_download_url":"https://github.com/rustdesk/rustdesk/releases/download/9.8.7/rustdesk-9.8.7-x86_64.exe"}'
    "]}"
)

MOCKS = {
    "curl": r'''
url="${@: -1}"
out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
if [ "$url" = "https://api.github.com/repos/rustdesk/rustdesk/releases/latest" ]; then
  echo release >>"$TEST_EVENTS"
  if [ "$TEST_SCENARIO" = "missing-asset" ]; then echo '{"tag_name":"9.8.7","assets":[]}'; else cat "$TEST_RELEASE"; fi
  exit 0
fi
echo "download:$url" >>"$TEST_EVENTS"
[ "$TEST_SCENARIO" = "download-failure" ] && exit 22
echo "mock dmg" >"$out"
''',
    "hdiutil": r'''
if [ "$1" = "attach" ]; then
  while [ $# -gt 0 ]; do [ "$1" = "-mountpoint" ] && mount="$2"; shift; done
  echo attach >>"$TEST_EVENTS"
  mkdir -p "$mount/RustDesk.app/Contents/MacOS"
  cp "$TEST_MOCKS/rustdesk" "$mount/RustDesk.app/Contents/MacOS/RustDesk"
fi
''',
    "ditto": 'echo install >>"$TEST_EVENTS"; cp -R "$1" "$2"',
    # "bootstrap <domain>" or "kickstart <service>", without the plist path or -k.
    "launchctl": 'if [ "$1" = bootstrap ]; then echo "bootstrap $2"; else echo "$1 ${@: -1}"; fi >>"$TEST_EVENTS"',
    "stat": 'echo 501',
    "id": 'echo 0',
    "uname": 'echo "$TEST_ARCH"',
    "chown": ':',
    "xattr": ':',
    "sleep": f'exec {SLEEP} 0.01',
    "rustdesk": r'''
echo "$1" >>"$TEST_EVENTS"
case "$1" in
  --get-id) echo "123 456 789" ;;
  --config)
    [ "$2" = "config with spaces" ] || exit 9
    [ "$TEST_SCENARIO" = "config-failure" ] && exit 1
    [ "$TEST_SCENARIO" = "command-timeout" ] && exec "$TEST_REAL_SLEEP" 30
    ;;
esac
exit 0
''',
}


def run_script(tmp_path, scenario, arch="arm64", override="", installed=None):
    mocks = tmp_path / "mocks"
    mocks.mkdir()
    for name, body in MOCKS.items():
        path = mocks / name
        path.write_text("#!" + BASH + "\n" + body)
        path.chmod(0o755)
    root = tmp_path / "root"
    (root / "Library/LaunchDaemons").mkdir(parents=True)
    (root / "Library/LaunchAgents").mkdir(parents=True)
    if installed if installed is not None else scenario == "installed":
        app = root / "Applications/RustDesk.app/Contents/MacOS"
        app.mkdir(parents=True)
        shutil.copy(mocks / "rustdesk", app / "RustDesk")
        (root / "Library/LaunchDaemons/com.carriez.RustDesk_service.plist").write_text("existing")
        (root / "Library/LaunchAgents/com.carriez.RustDesk_server.plist").write_text("existing")
    (root / "Applications").mkdir(exist_ok=True)
    script = tmp_path / "script.sh"
    script.write_text(
        SCRIPT.read_text().replace("/Applications/", f"{root}/Applications/").replace("/Library/", f"{root}/Library/")
    )
    release = tmp_path / "release.json"
    release.write_text(RELEASE)
    events = tmp_path / "events"
    events.write_text("")
    env = {
        **os.environ,
        "PATH": f"{mocks}:{os.environ.get('PATH', '')}",
        "TMPDIR": str(tmp_path),
        "TEST_EVENTS": str(events),
        "TEST_MOCKS": str(mocks),
        "TEST_RELEASE": str(release),
        "TEST_REAL_SLEEP": SLEEP,
        "TEST_SCENARIO": scenario,
        "TEST_ARCH": arch,
        "RUSTDESK_INSTALLER_URL": override,
        "RUSTDESK_CFG": "config with spaces",
        "MYPORTAL_RUSTDESK_PASSWORD": "secret-session-password",
    }
    result = subprocess.run([BASH, str(script)], env=env, capture_output=True, text=True, timeout=60)
    assert "secret-session-password" not in result.stdout + result.stderr
    return result, events.read_text().splitlines(), root


def test_installed_client_is_configured_without_a_download(tmp_path):
    result, events, root = run_script(tmp_path, "installed")
    assert result.returncode == 0, result.stderr
    assert events == [
        "bootstrap system", "bootstrap gui/501", "--config",
        "kickstart system/com.carriez.RustDesk_service", "kickstart gui/501/com.carriez.RustDesk_server",
        "--password", "--get-id",
    ]
    assert "##myportal[session.id]=123456789" in result.stdout
    # The existing launchd files are left alone.
    assert (root / "Library/LaunchDaemons/com.carriez.RustDesk_service.plist").read_text() == "existing"


@pytest.mark.parametrize("arch,asset", [("arm64", "aarch64"), ("x86_64", "x86_64")])
def test_missing_client_is_installed_before_configuration(tmp_path, arch, asset):
    result, events, root = run_script(tmp_path, "missing", arch)
    assert result.returncode == 0, result.stderr
    assert events == [
        "release", f"download:https://github.com/rustdesk/rustdesk/releases/download/9.8.7/rustdesk-9.8.7-{asset}.dmg",
        "attach", "install", "bootstrap system", "bootstrap gui/501", "--config",
        "kickstart system/com.carriez.RustDesk_service", "kickstart gui/501/com.carriez.RustDesk_server",
        "--password", "--get-id",
    ]
    assert f"Installing RustDesk 9.8.7 ({asset})" in result.stdout
    assert "##myportal[session.id]=123456789" in result.stdout
    assert (root / "Applications/RustDesk.app/Contents/MacOS/RustDesk").exists()
    daemon = (root / "Library/LaunchDaemons/com.carriez.RustDesk_service.plist").read_text()
    agent = (root / "Library/LaunchAgents/com.carriez.RustDesk_server.plist").read_text()
    assert "<string>com.carriez.RustDesk_service</string>" in daemon and "--service" in daemon
    assert "<string>com.carriez.RustDesk_server</string>" in agent and "<string>--server</string>" in agent


def test_installer_override_skips_github_discovery(tmp_path):
    result, events, _ = run_script(tmp_path, "missing", override="https://downloads.example.com/custom.dmg")
    assert result.returncode == 0, result.stderr
    assert events[0] == "download:https://downloads.example.com/custom.dmg"
    assert "release" not in events


@pytest.mark.parametrize("scenario,message", [
    ("download-failure", "Could not download the RustDesk installer."),
    ("missing-asset", "no unique macOS aarch64"),
    ("config-failure", "failed during: server configuration"),
    ("command-timeout", "timed out during: server configuration"),
])
def test_failures_return_an_error_without_a_ready_session(tmp_path, scenario, message):
    result, events, _ = run_script(tmp_path, scenario, installed="config" in scenario or "timeout" in scenario)
    assert result.returncode != 0
    assert message in result.stderr
    assert "--password" not in events
    assert "##myportal[session.id]" not in result.stdout
