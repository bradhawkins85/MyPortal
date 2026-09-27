"""Regression tests for JavaScript loaded by the shared portal layout."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HTMX_PATH = REPOSITORY_ROOT / "app/static/vendor/htmx/htmx-1.9.10.min.js"
HTMX_SHA256 = "b3bdcf5c741897a53648b1207fff0469a0d61901429ba1f6e88f98ebd84e669e"
REMOTE_SCRIPT = re.compile(r"<script\b[^>]*\bsrc=[\"']https?://", re.IGNORECASE)


def test_base_template_loads_the_vendored_htmx_asset() -> None:
    """The shared portal layout must not obtain htmx from a remote origin."""
    template = (REPOSITORY_ROOT / "app/templates/base.html").read_text()

    assert "static/vendor/htmx/htmx-1.9.10.min.js" in template
    assert "unpkg.com" not in template
    assert "<script src=\"http://" not in template
    assert "<script src=\"https://" not in template


def test_vendored_htmx_matches_the_reviewed_bytes() -> None:
    """Catch accidental or unreviewed changes to code used on secret-bearing pages."""
    assert hashlib.sha256(HTMX_PATH.read_bytes()).hexdigest() == HTMX_SHA256


def test_jinja_templates_do_not_load_remote_scripts() -> None:
    """Keep third-party JavaScript out of authenticated and secret-bearing views."""
    template_root = REPOSITORY_ROOT / "app/templates"
    offenders = [
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in template_root.rglob("*.html")
        if REMOTE_SCRIPT.search(path.read_text())
    ]

    assert offenders == []
