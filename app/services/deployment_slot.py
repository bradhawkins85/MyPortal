"""Blue/green slot awareness for background work.

In a blue/green deployment both slots keep running after a cutover: the slot
nginx no longer routes to stays up on the previous release so it can be rolled
back to.  Its scheduler must not keep running jobs, or work is done with stale
code (for example a queued request picked up by the old release).

``scripts/upgrade.sh`` records the live slot in the nginx upstream include,
which it writes world-readable as::

    server 127.0.0.1:8001 max_fails=1 fail_timeout=5s;
    server 127.0.0.1:8002 down;

A process whose ``APP_INSTANCE_ID`` slot is not the live one is the standby.
Anything that cannot be determined (no slot configured, file missing or
unrecognised) is treated as serving, so single-instance and development
installs, and blue/green installs from before this check, keep running jobs.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import log_info, log_warning

DEFAULT_UPSTREAM_FILE = "/etc/nginx/conf.d/myportal-active.inc"
SLOT_PORTS = {"blue": 8001, "green": 8002}
_ACTIVE_SERVER = re.compile(r"^\s*server\s+127\.0\.0\.1:(\d+)\s+max_fails\b", re.MULTILINE)
_CACHE_SECONDS = 5.0

_cache: tuple[float, bool] | None = None
_last_state: bool | None = None


def current_slot() -> str | None:
    slot = get_settings().app_instance_id.strip().lower()
    return slot if slot in SLOT_PORTS else None


def upstream_file() -> Path:
    return Path(os.getenv("MYPORTAL_NGINX_UPSTREAM_FILE") or DEFAULT_UPSTREAM_FILE)


def active_slot() -> str | None:
    """Return the slot nginx routes to, or ``None`` when it cannot be read."""
    try:
        text = upstream_file().read_text(encoding="utf-8")
    except OSError:
        return None
    ports = {int(port) for port in _ACTIVE_SERVER.findall(text)}
    if len(ports) != 1:
        return None
    port = ports.pop()
    return next((slot for slot, slot_port in SLOT_PORTS.items() if slot_port == port), None)


def is_serving_slot() -> bool:
    """Return whether this process may run scheduled background jobs."""
    global _cache, _last_state
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_SECONDS:
        return _cache[1]
    slot = current_slot()
    active = active_slot() if slot else None
    serving = slot is None or active is None or slot == active
    if slot and active is None and _last_state is None:
        log_warning(
            "Blue/green live slot could not be read; scheduled jobs run on this slot",
            slot=slot,
            upstream_file=str(upstream_file()),
        )
    if slot and active is not None and serving != _last_state:
        log_info(
            "Scheduled jobs enabled: this slot is live" if serving
            else "Scheduled jobs paused: this slot is the blue/green standby",
            slot=slot,
            active_slot=active,
        )
    _last_state = serving
    _cache = (now, serving)
    return serving


def reset_cache() -> None:
    """Forget the cached decision (tests and diagnostics)."""
    global _cache, _last_state
    _cache = None
    _last_state = None
