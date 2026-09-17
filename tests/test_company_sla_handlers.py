from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.datastructures import FormData

from app.features.companies import handlers
from app.repositories import slas as sla_repo


@pytest.mark.asyncio
async def test_admin_create_sla_template_rejects_non_numeric_windows(monkeypatch):
    request = MagicMock()
    request.form = AsyncMock(
        return_value=FormData(
            [
                ("name", "Standard"),
                ("description", "Default"),
                ("priority", "critical"),
                ("responseMinutes", "ten"),
                ("resolutionMinutes", "30"),
                ("pauseStatus", "waiting_on_client"),
                ("enabled", "on"),
            ]
        )
    )

    require_super_admin_page = AsyncMock(return_value=({"id": 1}, None))
    create_template = AsyncMock()
    main_module = SimpleNamespace(_require_super_admin_page=require_super_admin_page)

    monkeypatch.setattr(handlers, "_main", lambda: main_module)
    monkeypatch.setattr(sla_repo, "create_template", create_template)

    response = await handlers.admin_create_sla_template(request)

    require_super_admin_page.assert_awaited_once_with(request)
    create_template.assert_not_awaited()
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/sla-templates?error=invalid"
