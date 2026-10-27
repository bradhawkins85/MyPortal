"""Run the Linux installer with sandboxed paths and a stub systemd service."""

import os
from pathlib import Path
import subprocess

import pytest


INSTALLER = Path(__file__).resolve().parents[1] / "tray/installer/linux/install.sh"


@pytest.mark.parametrize(
    "session,override,previous_mode,expected",
    [
        (None, None, None, "headless"),
        ("/usr/share/xsessions/gnome.desktop", None, None, "desktop"),
        ("/usr/share/wayland-sessions/plasma.desktop", None, None, "desktop"),
        ("/usr/local/share/xsessions/xfce.desktop", None, None, "desktop"),
        ("/usr/local/share/wayland-sessions/sway.desktop", None, None, "desktop"),
        (None, "--desktop", None, "desktop"),
        ("/usr/share/xsessions/gnome.desktop", "--headless", None, "headless"),
        (None, None, "desktop", "desktop"),
        ("/usr/share/xsessions/gnome.desktop", None, "headless", "headless"),
        (None, "--headless", "desktop", "headless"),
        (None, "--desktop", "headless", "desktop"),
    ],
)
def test_linux_install_mode(tmp_path, session, override, previous_mode, expected):
    # Redirect all absolute installation/detection paths into this test's root.
    source = INSTALLER.read_text()
    for prefix in ("/opt/", "/etc/", "/usr/"):
        source = source.replace(prefix, str(tmp_path) + prefix)
    installer = tmp_path / "install.sh"
    installer.write_text(source)
    (tmp_path / "uninstall.sh").write_text("#!/bin/sh\nexit 0\n")
    binaries = tmp_path / "amd64"
    binaries.mkdir()
    (binaries / "myportal-tray-service").write_text("#!/bin/sh\nexit 0\n")
    (binaries / "myportal-tray-ui").write_text("#!/bin/sh\nexit 0\n")

    commands = tmp_path / "bin"
    commands.mkdir()
    for name, body in {
        "id": "echo 0",
        "uname": "echo x86_64",
        # A headless machine can still default to graphical.target.
        "systemctl": 'if [ "$1" = get-default ]; then echo graphical.target; fi',
    }.items():
        command = commands / name
        command.write_text("#!/bin/sh\n" + body + "\n")
        command.chmod(0o755)

    if session:
        session_file = tmp_path / session.lstrip("/")
        session_file.parent.mkdir(parents=True)
        session_file.write_text("[Desktop Entry]\nName=Desktop\n")

    installed_ui = tmp_path / "opt/myportal-tray/myportal-tray-ui"
    autostart = tmp_path / "etc/xdg/autostart/myportal-tray.desktop"
    args = ["--url", "https://portal.example.com", "--token", "test-token"]
    if previous_mode:
        config = tmp_path / "etc/myportal-tray/tray.env"
        config.parent.mkdir(parents=True)
        config.write_text("MYPORTAL_URL=https://portal.example.com\n")
        args = ["--upgrade"]
        if previous_mode == "desktop":
            installed_ui.parent.mkdir(parents=True)
            installed_ui.write_text("old UI")
            autostart.parent.mkdir(parents=True)
            autostart.write_text("old autostart")
    if override:
        args.append(override)

    env = dict(os.environ, PATH=str(commands) + os.pathsep + os.defpath)
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP", "DESKTOP_SESSION"):
        env.pop(name, None)
    result = subprocess.run(
        ["sh", str(installer), *args], env=env, capture_output=True, text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert f"({expected}, amd64)" in result.stdout
    assert (tmp_path / "opt/myportal-tray/myportal-tray-service").is_file()
    assert installed_ui.is_file() == (expected == "desktop")
    assert autostart.is_file() == (expected == "desktop")
