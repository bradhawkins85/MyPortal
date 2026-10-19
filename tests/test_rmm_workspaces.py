"""Patch management and checks follow the multi-section settings workspace."""
from __future__ import annotations

import re
from pathlib import Path

import jinja2

from app.services import rmm_checks, rmm_patching

TEMPLATES = Path("app/templates")
STUB_BASE = (
    "{% block header_actions %}{% endblock %}{% block content %}{% endblock %}"
    "{% block scripts %}{% endblock %}"
)


def _env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.ChoiceLoader(
            [jinja2.DictLoader({"base.html": STUB_BASE}), jinja2.FileSystemLoader(str(TEMPLATES))]
        ),
        autoescape=True,
    )
    env.globals["static_url"] = lambda path: path
    return env


def _check(check_id: int, check_type: str, **counts: int) -> dict:
    return {
        "id": check_id, "name": f"Check {check_id}", "check_type": check_type,
        "type_label": rmm_checks.CHECK_TYPES[check_type], "scope_label": "Company", "description": "x",
        "action": "none", "action_label": "Record", "cooldown_minutes": 0, "counts": counts,
        "last_summary": "", "is_enabled": True, "can_edit": False, "replaced": False, "asset_id": None,
    }


def test_checks_page_groups_checks_into_attention_all_and_type_sections():
    checks = [_check(1, "disk", failing=2), _check(2, "disk", passing=3), _check(3, "ping", passing=1)]
    html = _env().get_template("rmm/checks.html").render(
        company={"name": "Contoso"}, checks=checks, can_edit=False, editing=None,
        check_types=rmm_checks.CHECK_TYPES, csrf_token="t",
    )

    assert "data-ae-workspace" in html and 'data-ae-default="attention"' in html
    for key in ("attention", "all", "type-disk", "type-ping"):
        assert f'data-ae-section="{key}"' in html and f'data-ae-link="{key}"' in html, key
    assert 'data-ae-section="type-cpu"' not in html
    attention = html[html.index('id="rmm-checks-attention"'):html.index('id="rmm-checks-all"')]
    assert 'data-rmm-check="1"' in attention and 'data-rmm-check="2"' not in attention
    assert "data-ae-savebar" not in html
    assert "asset_edit_sections.js" in html


def test_patching_editor_is_a_section_with_a_save_bar():
    editing = {**rmm_patching.DEFAULTS, "id": 4, "name": "Weekly", "scope": "company", "kind": "windows",
               "categories": [], "target_mode": "all", "target_rules": None, "timezone": "UTC", "cron": "0 2 * * 0"}
    html = _env().get_template("rmm/patching.html").render(
        company={"id": 1, "name": "Contoso"}, policies=[], blocks=[], can_edit=True, can_manage_sources=True,
        is_super_admin=False, editing=editing, errors={}, categories=sorted(rmm_patching.CATEGORIES), csrf_token="t",
    )

    assert 'data-ae-initial="editor" data-ae-form="patch-policy-form"' in html
    for key in ("policies", "editor", "blocked-updates", "chocolatey-sources"):
        assert f'data-ae-section="{key}"' in html and f'data-ae-link="{key}"' in html, key
    assert re.search(r'<form class="ce-section__body form rmm-check-form" id="patch-policy-form"', html)
    assert 'form="patch-policy-form"' in html and "data-ae-discard" in html
    assert 'href="/admin/companies/1/chocolatey-sources"' in html
