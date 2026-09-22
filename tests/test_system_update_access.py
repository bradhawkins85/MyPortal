from __future__ import annotations

import inspect
from pathlib import Path

from fastapi.params import Depends

from app.api.dependencies.auth import require_super_admin
from app.api.routes.scheduler import get_system_update, list_system_updates


def test_system_update_api_requires_global_administrator():
    for endpoint in (list_system_updates, get_system_update):
        dependencies = [
            parameter.default.dependency
            for parameter in inspect.signature(endpoint).parameters.values()
            if isinstance(parameter.default, Depends)
        ]
        assert require_super_admin in dependencies


def test_system_update_history_ui_supports_filter_sort_and_detail():
    root = Path(__file__).parents[1]
    history = (root / "app/templates/admin/system_updates.html").read_text()
    detail = (root / "app/templates/admin/system_update_detail.html").read_text()
    assert 'data-table-filter="system-updates-table"' in history
    assert 'data-table-id="system-updates"' in history
    assert 'data-sort="date"' in history
    assert "/admin/system-updates/{{ update.id }}" in history
    assert "update.error" in detail
    assert "update.output" in detail
