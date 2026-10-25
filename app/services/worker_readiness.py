"""Whole-service readiness for Uvicorn's ``--workers`` mode.

Uvicorn runs ``--workers N`` as N processes sharing one listening socket, and
each starts accepting connections only once its own startup has finished. A
``/readyz`` answer therefore speaks for the one worker that took the request:
the others may still be starting, competing with it for CPU and the database,
and if any of them fails to start Uvicorn stops the whole service, including
the workers already serving. A blue/green cutover gated on that first answer
moves traffic to a service that is still starting, or about to stop.

Each worker records that it has started in a directory shared only by the
workers of the same Uvicorn parent process, and ``/readyz`` reports the service
ready once every worker has started. That is remembered (a marker in the same
directory), so a worker restarted later does not take a serving service out of
rotation. Workers that have still not all started ``GRACE_SECONDS`` after the
first one did no longer hold the service back, so a stuck worker cannot keep a
running service out of rotation for good.

The directory is in the temporary directory, which systemd's PrivateTmp and
containers keep private to the service, and is named after the parent's PID and
start time, so markers from an earlier run never count. When none of this can
be determined (a single process, a parent without ``--workers``, no /proc, an
unusable directory), the check does not apply and the service is ready when
this process is.
"""

from __future__ import annotations

import multiprocessing
import os
import stat
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.core.logging import log_info, log_warning

GRACE_SECONDS = 180.0
_READY_MARKER = "ready"

_resolved = False
_directory: Path | None = None
_expected: int | None = None
_service_ready = False


def configured_workers(argv: list[str]) -> int | None:
    """Return the ``--workers`` count from a Uvicorn command line, if any."""
    if not any("uvicorn" in arg for arg in argv):
        return None
    for index, arg in enumerate(argv):
        if arg == "--workers":
            value = argv[index + 1] if index + 1 < len(argv) else ""
        elif arg.startswith("--workers="):
            value = arg.partition("=")[2]
        else:
            continue
        return int(value) if value.isdigit() and int(value) > 1 else None
    return None


def _base_directory() -> Path:
    return Path(tempfile.gettempdir())


def _parent_details() -> tuple[int, list[str], str] | None:
    """Return the PID, command line and start time of Uvicorn's supervisor."""
    # Uvicorn starts its workers with multiprocessing; a single-process server
    # has no multiprocessing parent.
    if multiprocessing.parent_process() is None:
        return None
    ppid = os.getppid()
    try:
        argv = Path(f"/proc/{ppid}/cmdline").read_bytes().split(b"\0")
        fields = Path(f"/proc/{ppid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    except (OSError, IndexError, UnicodeDecodeError):
        return None
    # The start time is field 22 of /proc/PID/stat; the fields after the
    # command name start at field 3.
    if len(fields) < 20:
        return None
    return ppid, [arg.decode("utf-8", "replace") for arg in argv if arg], fields[19]


def _resolve() -> tuple[Path, int] | None:
    global _resolved, _directory, _expected
    if not _resolved:
        _resolved = True
        details = _parent_details()
        expected = configured_workers(details[1]) if details else None
        if details and expected:
            ppid, _argv, started = details
            _directory = _base_directory() / f"myportal-workers-{ppid}-{started}"
            _expected = expected
    if _directory is None or _expected is None:
        return None
    return _directory, _expected


def _private_directory(path: Path) -> bool:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        return False
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077


def _create(path: Path, *, exclusive: bool = False) -> bool:
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        return False
    os.close(fd)
    return True


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _started_workers(directory: Path) -> tuple[int, float | None] | None:
    """Return how many running workers have started, and when the first did."""
    started = 0
    first: float | None = None
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            marked_at = entry.stat(follow_symlinks=False).st_mtime
        except OSError:
            continue
        first = marked_at if first is None else min(first, marked_at)
        if _alive(int(entry.name)):
            started += 1
    return started, first


def _record_ready(directory: Path, log: Callable[..., None], message: str, **meta: Any) -> None:
    global _service_ready
    _service_ready = True
    # Exclusive, so only the worker that records it logs it.
    if _create(directory / _READY_MARKER, exclusive=True):
        log(message, **meta)


def mark_started() -> None:
    """Record that this worker has finished startup."""
    resolved = _resolve()
    if resolved is None:
        return
    directory, _expected_workers = resolved
    if _private_directory(directory) and _create(directory / str(os.getpid())):
        # The last worker to start records the service as ready straight away.
        status()


def status() -> str | None:
    """Return ``None`` when the check does not apply, ``"ok"`` once every worker
    of this service has started, or a description of what it is waiting for."""
    global _service_ready
    if _service_ready:
        return "ok"
    resolved = _resolve()
    if resolved is None:
        return None
    directory, expected = resolved
    if not _private_directory(directory):
        return None
    if (directory / _READY_MARKER).exists():
        _service_ready = True
        return "ok"
    counted = _started_workers(directory)
    if counted is None:
        return None
    started, first = counted
    if started >= expected:
        _record_ready(directory, log_info, "All application workers started", workers=expected)
        return "ok"
    if first is not None and time.time() - first >= GRACE_SECONDS:
        _record_ready(
            directory,
            log_warning,
            "Not all application workers started; serving with the workers that did",
            started=started,
            workers=expected,
            waited_seconds=GRACE_SECONDS,
        )
        return "ok"
    return f"pending: {started} of {expected} workers started"
