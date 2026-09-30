"""Guard app.css against merges that leave rule blocks unbalanced.

An unclosed brace nests every later rule inside the previous block, so whole
page stylesheets silently stop applying in the browser.
"""
from __future__ import annotations

import re
from pathlib import Path

APP_CSS = Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "app.css"


def test_app_css_braces_balance():
    source = re.sub(r"/\*.*?\*/", "", APP_CSS.read_text(encoding="utf-8"), flags=re.S)
    depth = 0
    for line_number, line in enumerate(source.splitlines(), 1):
        depth += line.count("{") - line.count("}")
        assert depth >= 0, f"unexpected '}}' on line {line_number}"
    assert depth == 0, f"{depth} unclosed block(s) in app.css"
