"""Charts of a device's stats for the asset page.

The samples come from the separate stats database (:mod:`app.core.stats_database`)
and are drawn as inline SVG on the server, so the page needs no chart library.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.core.config import get_settings
from app.core.logging import log_warning
from app.core.stats_database import stats_db
from app.repositories import rmm_stats as repo

RANGES = {"24h": ("Last 24 hours", timedelta(days=1)), "7d": ("Last 7 days", timedelta(days=7)),
          "30d": ("Last 30 days", timedelta(days=30))}
DEFAULT_RANGE = "24h"
WIDTH = 600
HEIGHT = 160
MAX_POINTS = 240
MAX_SERIES = 6
GB = 1024 ** 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _nice_max(value: float) -> float:
    """A round axis maximum at or above ``value``."""

    if value <= 0:
        return 1.0
    magnitude = 10 ** (len(str(int(value))) - 1)
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return float(step * magnitude)
    return float(10 * magnitude)


def _label(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _average(points: list[tuple[datetime, float]], since: datetime, span: float) -> list[tuple[datetime, float]]:
    """Average points into at most MAX_POINTS buckets across the range."""

    if len(points) <= MAX_POINTS:
        return points
    buckets: dict[int, list[tuple[datetime, float]]] = defaultdict(list)
    for when, value in points:
        index = min(MAX_POINTS - 1, int((when - since).total_seconds() / span * MAX_POINTS))
        buckets[index].append((when, value))
    return [
        (items[len(items) // 2][0], sum(value for _when, value in items) / len(items))
        for _index, items in sorted(buckets.items())
    ]


def chart(title: str, unit: str, series: Iterable[tuple[str, list[tuple[datetime, float]]]], *,
          since: datetime, until: datetime, gap: timedelta, fixed_max: float | None = None) -> dict[str, Any] | None:
    """One chart: SVG polyline points for each series in a WIDTH x HEIGHT box.

    A line breaks where samples are further apart than ``gap``, so a device
    that was off shows as a gap rather than a straight line."""

    span = max((until - since).total_seconds(), 1.0)
    lines = [(name, _average(sorted(points), since, span)) for name, points in series if points][:MAX_SERIES]
    if not lines:
        return None
    peak = max(value for _name, points in lines for _when, value in points)
    top = fixed_max if fixed_max is not None else _nice_max(peak * 1.1)
    drawn = []
    for index, (name, points) in enumerate(lines):
        segments: list[list[str]] = []
        previous: datetime | None = None
        for when, value in points:
            if previous is None or when - previous > gap:
                segments.append([])
            x = (when - since).total_seconds() / span * WIDTH
            y = HEIGHT - max(0.0, min(value, top)) / top * HEIGHT
            segments[-1].append(f"{x:.1f},{y:.1f}")
            previous = when
        latest = points[-1][1]
        drawn.append({
            "name": name,
            "colour": index,
            "segments": [" ".join(segment) for segment in segments],
            # A single point in a segment draws as a dot.
            "dots": [segment[0] for segment in segments if len(segment) == 1],
            "latest": f"{_label(latest)} {unit}".strip(),
            "peak": f"{_label(max(value for _when, value in points))} {unit}".strip(),
        })
    return {
        "title": title,
        "unit": unit,
        "width": WIDTH,
        "height": HEIGHT,
        "top": f"{_label(top)} {unit}".strip(),
        "middle": f"{_label(top / 2)} {unit}".strip(),
        "since": since.isoformat(),
        "until": until.isoformat(),
        "series": drawn,
    }


def build_charts(samples: Iterable[dict[str, Any]], *, since: datetime, until: datetime) -> list[dict[str, Any]]:
    by_metric: dict[str, dict[str, list[tuple[datetime, float]]]] = defaultdict(lambda: defaultdict(list))
    for sample in samples:
        by_metric[str(sample["metric"])][str(sample.get("instance") or "")].append(
            (_as_datetime(sample["sampled_at"]), float(sample["value"])))
    gap = timedelta(minutes=get_settings().stats_sample_minutes * 3)
    common = {"since": since, "until": until, "gap": gap}
    charts = [
        chart("CPU", "%", [("CPU", by_metric["cpu_percent"][""])], fixed_max=100, **common),
        chart("Memory used", "%", [("Memory", by_metric["memory_used_percent"][""])], fixed_max=100, **common),
        chart("Disk free", "%", sorted(by_metric["disk_free_percent"].items()), fixed_max=100, **common),
    ]
    # Network: every interface added together, in Mbps.
    totals: dict[str, dict[datetime, float]] = {"Received": defaultdict(float), "Sent": defaultdict(float)}
    for metric, label in (("net_in_bps", "Received"), ("net_out_bps", "Sent")):
        for points in by_metric[metric].values():
            for when, value in points:
                totals[label][when] += value / 1_000_000
    charts.append(chart("Network", "Mbps", [(label, sorted(points.items())) for label, points in totals.items()],
                        **common))
    charts.append(chart("Ping", "ms", sorted(by_metric["ping_ms"].items()), **common))
    return [item for item in charts if item]


async def asset_stats_context(asset_id: int, range_key: str | None = None) -> dict[str, Any]:
    """The asset page's Machine stats section."""

    key = range_key if range_key in RANGES else DEFAULT_RANGE
    until = _utcnow()
    since = until - RANGES[key][1]
    status = stats_db.status()
    context: dict[str, Any] = {
        "database": {"configured": status["configured"], "connected": status["connected"]},
        "range": key,
        "ranges": [{"key": name, "label": label} for name, (label, _span) in RANGES.items()],
        "charts": [],
        "sample_minutes": get_settings().stats_sample_minutes,
        "retention_days": get_settings().stats_retention_days,
    }
    if not await stats_db.ensure_connected():
        return context
    context["database"]["connected"] = True
    try:
        samples = await repo.list_samples(asset_id=asset_id, since=since)
    except Exception as exc:  # the asset page must not fail with the stats database
        log_warning("Could not read RMM device stats for the asset page", asset_id=asset_id, error=str(exc))
        return context
    context["charts"] = build_charts(samples, since=since, until=until)
    return context
