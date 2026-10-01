"""Regression checks for the platform-wide searchable select enhancement."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"
SCRIPT = REPO_ROOT / "app" / "static" / "js" / "searchable_select.js"
STYLES = REPO_ROOT / "app" / "static" / "css" / "app.css"


def test_base_layout_loads_searchable_select_before_page_scripts():
    base = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
    include = "static_url('/static/js/searchable_select.js')"
    assert include in base
    assert base.index(include) < base.index("static_url('/static/js/main.js')")
    assert base.index(include) < base.index("{% block scripts %}")


def test_no_template_asks_users_to_ctrl_click_multi_selects():
    pattern = re.compile(r"hold\s+(down\s+)?(ctrl|control|cmd|command|⌘|shift)", re.IGNORECASE)
    offenders = [
        str(path.relative_to(TEMPLATES_DIR))
        for path in TEMPLATES_DIR.rglob("*.html")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_searchable_select_styles_keep_native_select_focusable():
    css = STYLES.read_text(encoding="utf-8")
    block = css[css.index("select.ss-native {"):]
    block = block[: block.index("}")]
    # display:none would stop browsers from reporting required-field errors.
    assert "display: none" not in block
    assert "clip-path" in block


def test_searchable_select_script_parses():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    result = subprocess.run([node, "--check", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_admin_ticket_detail_dropdowns_are_all_searchable():
    # Short lists (priority, change type, labour type) stay native by default,
    # so the ticket page opts every dropdown in to keep the fields consistent.
    template = (TEMPLATES_DIR / "admin" / "ticket_detail.html").read_text(encoding="utf-8")
    selects = re.findall(r"<select\b[^>]*>", template)
    assert selects
    offenders = [tag for tag in selects if 'data-searchable="on"' not in tag]
    assert offenders == []
