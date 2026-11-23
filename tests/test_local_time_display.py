"""Timestamps render in the viewer's browser time zone, not UTC."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from jinja2 import Environment

from app.core.local_time import local_time, register, utc_iso, utc_text

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"


def test_utc_iso_marks_naive_database_values_as_utc():
    assert utc_iso(datetime(2026, 11, 23, 10, 15, 30, 123)) == "2026-11-23T10:15:30Z"
    assert utc_iso("2026-11-23 10:15:30") == "2026-11-23T10:15:30Z"
    assert utc_iso("2026-11-23T10:15:30Z") == "2026-11-23T10:15:30Z"


def test_utc_iso_converts_aware_values_to_utc():
    brisbane = timezone(timedelta(hours=10))
    assert utc_iso(datetime(2026, 11, 23, 20, 0, tzinfo=brisbane)) == "2026-11-23T10:00:00Z"


def test_utc_iso_keeps_calendar_dates_and_handles_empty_values():
    assert utc_iso(date(2026, 11, 23)) == "2026-11-23"
    assert utc_iso("2026-11-23") == "2026-11-23"
    assert utc_iso(None) == ""
    assert utc_iso("") == ""
    assert utc_iso("not a date") == ""


def test_local_time_renders_localisable_span_with_utc_fallback():
    html = str(local_time(datetime(2026, 11, 23, 10, 15)))
    assert html == (
        '<span data-utc="2026-11-23T10:15:00Z" data-utc-format="datetime">'
        "2026-11-23 10:15 UTC</span>"
    )
    assert 'data-utc-format="date">2026-11-23</span>' in str(
        local_time(datetime(2026, 11, 23, 10, 15), "date")
    )


def test_local_time_empty_value_is_escaped_placeholder():
    assert str(local_time(None)) == "—"
    assert str(local_time(None, empty="<b>Never</b>")) == "&lt;b&gt;Never&lt;/b&gt;"


def test_utc_text_matches_fallback_the_browser_replaces():
    assert utc_text(datetime(2026, 11, 23, 10, 15)) == "2026-11-23 10:15 UTC"
    assert utc_text(None) == ""


def test_helpers_are_available_to_templates():
    env = Environment(autoescape=True)
    register(env)
    rendered = env.from_string("{{ local_time(value) }}|{{ value | utc_iso }}").render(
        value=datetime(2026, 1, 2, 3, 4)
    )
    assert rendered == (
        '<span data-utc="2026-01-02T03:04:00Z" data-utc-format="datetime">'
        "2026-01-02 03:04 UTC</span>|2026-01-02T03:04:00Z"
    )


def test_base_layout_loads_the_global_localiser_before_page_scripts():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    head = base.split("</head>", 1)[0]
    assert "/static/js/local_time.js" in head


def test_web_templates_do_not_format_timestamps_on_the_server():
    """Server-side strftime/astimezone shows UTC or the server's zone.

    PDF exports are rendered without a browser and keep explicit UTC labels;
    calendar dates and user-entered schedule values have no zone to convert.
    """

    allowed_fields = (
        "claim_date", "review_date", "due_date", "last_review_date",
        "release_date", "last_reviewed", "next_review", "training_date",
        "range_start", "range_end", "day.",
    )
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        if "pdf" in path.name:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "astimezone()" in line:
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
                continue
            for match in re.finditer(r"([\w\.]+)\.strftime\(", line):
                if not any(field in match.group(1) + "." for field in allowed_fields):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert offenders == []


@pytest.mark.parametrize("zone", ["Australia/Brisbane", "America/Los_Angeles"])
def test_local_time_js_node_suite(zone):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed to run the local time tests")
    result = subprocess.run(
        [node, "--test", str(ROOT / "tests" / "test_local_time.js")],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "TZ": zone},
    )
    assert result.returncode == 0, result.stdout + result.stderr
