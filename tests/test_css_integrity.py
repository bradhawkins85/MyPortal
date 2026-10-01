"""Guard against stylesheets broken by bad merges (unbalanced braces swallow later rules)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS_FILES = sorted(Path("app/static/css").glob("*.css"))


@pytest.mark.parametrize("path", CSS_FILES, ids=lambda path: path.name)
def test_stylesheet_braces_are_balanced(path: Path):
    source = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    source = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', source)
    depth = 0
    for line_number, line in enumerate(source.splitlines(), start=1):
        for char in line:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                assert depth >= 0, f"{path.name}:{line_number} closes a block that was never opened"
    assert depth == 0, f"{path.name} leaves {depth} block(s) unclosed"
