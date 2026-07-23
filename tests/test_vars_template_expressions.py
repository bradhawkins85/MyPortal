from __future__ import annotations

from datetime import date

import asyncio

from app.services import value_templates


def test_dollar_vars_now_local_date_renders_inside_json_payload():
    payload = {
        "subject": "GMP Backup Restore Testing - ${vars.now.local.date}",
        "priority": "normal",
    }

    rendered = asyncio.run(value_templates.render_value_async(payload, {}))

    assert rendered["subject"].startswith("GMP Backup Restore Testing - ")
    assert "${vars.now.local.date}" not in rendered["subject"]
    assert date.fromisoformat(rendered["subject"].rsplit(" - ", 1)[1])


def test_dollar_vars_date_supports_formatting_and_month_offsets(monkeypatch):
    class FixedDateTime(value_templates.datetime):
        @classmethod
        def now(cls, tz=None):
            base = cls(2026, 1, 15, 9, 30, tzinfo=tz)
            return base if tz is not None else base.replace(tzinfo=None)

    monkeypatch.setattr(value_templates, "datetime", FixedDateTime)

    assert (
        asyncio.run(
            value_templates.render_string_async(
                '${vars.now.local.date}.format("MMMM")', {}
            )
        )
        == "January"
    )
    assert (
        asyncio.run(
            value_templates.render_string_async(
                '${vars.now.local.date.last_month}.format("MMMM yyyy")', {}
            )
        )
        == "December 2025"
    )
    assert (
        asyncio.run(
            value_templates.render_string_async(
                '${vars.now.local.date.next(months=1)}.format("MMMM yyyy")', {}
            )
        )
        == "February 2026"
    )
