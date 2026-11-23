"""Parse and format the port lists used by discovered device port checks."""

from __future__ import annotations

import re
from collections.abc import Iterable

MAX_PORTS = 1024
_TOKEN = re.compile(r"^(\d{1,5})(?:-(\d{1,5}))?$")


def parse_port_spec(raw: str) -> list[int]:
    """Return the sorted, unique ports in ``443, 8000-8100``-style text.

    Raises ``ValueError`` with a message suitable for showing to the user.
    """
    tokens = [token for token in re.split(r"[,\s]+", raw or "") if token]
    if not tokens:
        raise ValueError("Enter at least one port, for example 443 or 8000-8100.")
    ports: set[int] = set()
    for token in tokens:
        match = _TOKEN.match(token)
        if not match:
            raise ValueError(
                f"“{token}” isn't a port or a range. Use a number like 443 or a range like 8000-8100."
            )
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if not (1 <= start <= 65535 and 1 <= end <= 65535):
            raise ValueError(f"“{token}” is outside 1–65535.")
        if end < start:
            raise ValueError(f"“{token}” runs backwards. Put the lower port first.")
        if end - start + 1 > MAX_PORTS:
            raise ValueError(f"Check at most {MAX_PORTS:,} ports at a time.")
        ports.update(range(start, end + 1))
        if len(ports) > MAX_PORTS:
            raise ValueError(f"Check at most {MAX_PORTS:,} ports at a time.")
    return sorted(ports)


def format_port_spec(ports: Iterable[int]) -> str:
    """Compress ports into ``22,443,8000-8002`` form."""
    ordered = sorted(set(int(port) for port in ports))
    parts: list[str] = []
    index = 0
    while index < len(ordered):
        start = end = ordered[index]
        while index + 1 < len(ordered) and ordered[index + 1] == end + 1:
            index += 1
            end = ordered[index]
        parts.append(str(start) if start == end else f"{start}-{end}")
        index += 1
    return ",".join(parts)
