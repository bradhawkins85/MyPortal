from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_import_order(*module_names: str) -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        f"""
        import importlib

        for module_name in {module_names!r}:
            importlib.import_module(module_name)

        import app.main as main_module
        from app.api.routes import bcp

        bcp.configure_page_rendering(
            build_base_context=main_module._build_base_context,
            templates=main_module.templates,
        )
        build_base_context, templates = bcp._get_page_rendering()
        assert build_base_context is main_module._build_base_context
        assert templates is main_module.templates
        """
    )
    env = os.environ.copy()
    env.setdefault("SESSION_SECRET", "test-session-secret")
    env.setdefault("TOTP_ENCRYPTION_KEY", "A" * 64)
    env.setdefault("DB_HOST", "localhost")
    env.setdefault("DB_USER", "user")
    env.setdefault("DB_PASSWORD", "password")
    env.setdefault("DB_NAME", "testdb")
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        env=env,
        text=True,
    )


@pytest.mark.parametrize(
    ("module_names"),
    [
        ("app.main", "app.api.routes.bcp"),
        ("app.api.routes.bcp", "app.main"),
        ("app.services.modules", "app.services.call_recordings"),
        ("app.services.call_recordings", "app.services.modules"),
        ("app.services.modules", "app.services.email", "app.services.smtp2go", "app.services.solidtime"),
        ("app.services.solidtime", "app.services.smtp2go", "app.services.email", "app.services.modules"),
    ],
)
def test_import_orders_do_not_raise_partially_initialised_module_errors(module_names):
    result = _run_import_order(*module_names)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_bcp_glossary_uses_configured_rendering_seam(monkeypatch):
    from app.api.routes import bcp

    request = MagicMock(spec=Request)
    templates_calls: list[tuple[str, dict[str, object]]] = []

    async def fake_require_bcp_view(_request: Request):
        return {"id": 7, "is_super_admin": True}, 101

    async def fake_build_base_context(_request: Request, user: dict[str, object], *, extra=None):
        return {"request": _request, "current_user": user, **(extra or {})}

    class FakeTemplates:
        def TemplateResponse(self, template_name: str, context: dict[str, object]):
            templates_calls.append((template_name, context))
            return {"template_name": template_name, "context": context}

    monkeypatch.setattr(bcp, "_require_bcp_view", fake_require_bcp_view)
    monkeypatch.setattr(
        bcp,
        "_page_rendering",
        (fake_build_base_context, FakeTemplates()),
    )

    response = await bcp.bcp_glossary(request)

    assert response["template_name"] == "bcp/glossary.html"
    assert templates_calls[0][1]["title"] == "BCP Glossary"
    assert templates_calls[0][1]["current_user"]["id"] == 7


@pytest.mark.asyncio
async def test_module_runtime_merges_service_defaults(monkeypatch):
    from app.services import module_runtime

    module_row = {
        "slug": "call-recordings",
        "enabled": True,
        "settings": {"recordings_path": "/srv/recordings"},
    }
    smtp2go_row = {
        "slug": "smtp2go",
        "enabled": True,
        "settings": {},
    }

    repo_mock = AsyncMock(side_effect=[module_row, smtp2go_row])
    monkeypatch.setattr(module_runtime.module_repo, "get_module", repo_mock)

    call_recordings_module = await module_runtime.get_module(
        "call-recordings", redact=False
    )
    smtp2go_settings = await module_runtime.get_module_settings("smtp2go")

    assert call_recordings_module is not None
    assert call_recordings_module["settings"]["recordings_path"] == "/srv/recordings"
    assert call_recordings_module["settings"]["phone_system_type"] == "generic"
    assert smtp2go_settings is not None
    assert smtp2go_settings["manage_url"] == "/admin/modules/smtp2go"
    assert smtp2go_settings["not_engaged_delay_seconds"] == 86400
