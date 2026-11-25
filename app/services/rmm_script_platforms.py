"""Platform selection shared by script execution and tray menus."""

from typing import Any, Mapping

from app.repositories import rmm as rmm_repo

SCRIPT_PLATFORMS = {
    "windows": "Windows", "darwin": "Mac", "linux": "Linux",
    "freebsd": "FreeBSD", "openbsd": "OpenBSD", "netbsd": "NetBSD",
}


def normalise_platform(value: Any) -> str:
    name = str(value or "").strip().lower()
    return {"mac": "darwin", "macos": "darwin", "osx": "darwin", "win32": "windows", "win": "windows"}.get(name, name)


def platform_allowed(script: Mapping[str, Any], agent: Mapping[str, Any]) -> bool:
    platforms = script.get("platforms")
    if platforms is None:
        platforms = rmm_repo.default_script_platforms(script.get("language"))
    return normalise_platform(agent.get("os")) in platforms
