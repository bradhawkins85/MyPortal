"""Regression tests for the company automation editor form."""

from pathlib import Path


def test_company_automation_editor_has_every_field_required_by_populator():
    """Editing must not abort because a field expected by automation.js is absent."""
    template = Path("app/templates/admin/company_edit.html").read_text()

    required_field_ids = (
        "task-id",
        "task-command",
        "task-cron",
        "task-description",
        "task-max-retries",
        "task-backoff",
        "task-active",
        "task-exclude-calendar",
    )

    for field_id in required_field_ids:
        assert f'id="{field_id}"' in template
