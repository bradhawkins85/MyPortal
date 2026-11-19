"""The shared 👍/👎 control for AI output."""
from __future__ import annotations

import re

import pytest

import app.main as main_module
from app.repositories.ai_quality import REASONS
from app.services.component_availability import ComponentAvailability


def _render(monkeypatch, disabled=frozenset(), **kwargs) -> str:
    policy = ComponentAvailability(disabled_feature_packs=frozenset(disabled))
    monkeypatch.setattr(main_module, "get_component_availability", lambda: policy)
    template = main_module.templates.env.from_string(
        '{% from "macros/ai_rating.html" import ai_rating %}{{ ai_rating(**kwargs) }}'
    )
    return template.render(kwargs=kwargs)


def test_renders_nothing_without_an_output(monkeypatch):
    assert _render(monkeypatch, response_id=None).strip() == ""


def test_renders_nothing_when_ai_quality_is_disabled(monkeypatch):
    assert _render(monkeypatch, {"ai_quality"}, response_id=11).strip() == ""


def test_works_as_a_plain_form(monkeypatch):
    html = _render(monkeypatch, response_id=11, csrf_token="tok", return_to="/tickets/7")
    assert 'method="post"' in html and 'action="/api/ai-quality/feedback"' in html
    assert 'name="_csrf" value="tok"' in html
    assert 'name="response_id" value="11"' in html
    assert 'name="return_to" value="/tickets/7"' in html
    assert re.search(r'<button[^>]*name="rating"[^>]*value="up"', html)
    assert re.search(r'<button[^>]*name="rating"[^>]*value="down"', html)
    assert 'role="status"' in html and 'aria-live="polite"' in html


def test_current_vote_is_pressed(monkeypatch):
    html = _render(monkeypatch, response_id=11, current_rating="down")
    down = re.search(r'<button[^>]*value="down"[^>]*>', html).group(0)
    up = re.search(r'<button[^>]*value="up"[^>]*>', html).group(0)
    assert 'aria-pressed="true"' in down
    assert 'aria-pressed="false"' in up


def test_every_reason_is_offered(monkeypatch):
    html = _render(monkeypatch, response_id=11)
    for reason in REASONS:
        assert f'value="{reason}"' in html, reason


def test_label_is_used_for_accessible_names(monkeypatch):
    html = _render(monkeypatch, response_id=11, label="Ticket summary")
    assert 'aria-label="Ticket summary was helpful"' in html
    assert 'aria-label="Ticket summary was not helpful"' in html


@pytest.mark.parametrize("script", ["ai_rating.js"])
def test_base_layout_loads_the_rating_script(script):
    from pathlib import Path

    assert f"/static/js/{script}" in Path("app/templates/base.html").read_text(encoding="utf-8")
