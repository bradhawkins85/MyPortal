"""Regression tests for the On/Off status cards in the automation editors."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_type_card_highlight_follows_checked_input_without_js():
    """The automation editors never add .is-selected, so the checked radio must highlight via CSS."""
    css = (ROOT / "app" / "static" / "css" / "app.css").read_text()
    match = re.search(r"([^{}]*\.scf-type-card\.is-selected[^{}]*)\{", css)
    assert match is not None
    selectors = [s.strip() for s in match.group(1).split(",")]
    assert ".scf-type-card.is-selected" in selectors
    assert ".scf-type-card:has(input:checked)" in selectors


def test_status_macro_renders_radio_inside_type_cards():
    template = (ROOT / "app" / "templates" / "admin" / "_automation_form_macros.html").read_text()
    assert re.search(r'<label class="scf-type-card">\s*<input type="radio" name="status" value="active"', template)
    assert re.search(r'<label class="scf-type-card">\s*<input type="radio" name="status" value="inactive"', template)
