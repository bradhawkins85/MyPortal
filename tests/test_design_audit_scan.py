"""The design audit must agree with the modal standard in the Design Guidelines."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUIDELINES = REPO_ROOT / "docs" / "wiki" / "developer" / "Design Guidelines.md"


def _load_scanner():
    spec = importlib.util.spec_from_file_location(
        "design_audit_scan", REPO_ROOT / "docs" / "design_audit_scan.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _modal_snippet_from_guidelines() -> str:
    text = GUIDELINES.read_text(encoding="utf-8")
    section = text.split("### Modals", 1)[1]
    return section.split("```html", 1)[1].split("```", 1)[0]


def test_guidelines_modal_example_passes_the_audit():
    scanner = _load_scanner()
    result = scanner.classify("example.html", _modal_snippet_from_guidelines())
    assert result["modals"] == "PASS", result["notes"]


def test_gold_reference_modal_passes_the_audit():
    scanner = _load_scanner()
    template = REPO_ROOT / "app" / "templates" / "admin" / "company_edit.html"
    text = template.read_text(encoding="utf-8")
    start = text.index('id="staff-custom-field-modal"')
    start = text.rindex("<div", 0, start)
    end = text.index('data-scf-delete-form', start)
    result = scanner.classify("admin/company_edit.html", text[start:end])
    assert result["modals"] == "PASS", result["notes"]


def test_dialog_modals_are_flagged():
    scanner = _load_scanner()
    snippet = (
        '<dialog class="modal" id="x" aria-labelledby="x-title">'
        '<button class="modal__close" data-modal-close></button>'
        '<h2 class="modal__title" id="x-title">X</h2></dialog>'
    )
    result = scanner.classify("example.html", snippet)
    assert result["modals"].startswith("FAIL")
    assert any("uses <dialog> not <div>" in note for note in result["notes"])


def test_guidelines_do_not_recommend_dialog_modals():
    text = GUIDELINES.read_text(encoding="utf-8")
    assert 'Use `<dialog class="modal">`' not in text
    assert "Prefer `<dialog>`" not in text
