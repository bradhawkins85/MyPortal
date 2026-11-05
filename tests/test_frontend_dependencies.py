"""Regression tests for JavaScript loaded by the shared portal layout."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HTMX_PATH = REPOSITORY_ROOT / "app/static/vendor/htmx/htmx-1.9.10.min.js"
HTMX_SHA256 = "b3bdcf5c741897a53648b1207fff0469a0d61901429ba1f6e88f98ebd84e669e"
REMOTE_SCRIPT = re.compile(r"<script\b[^>]*\bsrc=[\"']https?://", re.IGNORECASE)

# The RMM live tools terminal is served from vendored xterm.js, never a CDN.
XTERM_ASSETS = {
    "app/static/vendor/xterm/xterm-5.5.0.js": "7a88e46a58847d3f17109e3d48cc8aa056a01997d00ceb76035f63370712d858",
    "app/static/vendor/xterm/xterm-5.5.0.css": "ba8e6985669488981ccf40c0cefe3aba80722cb6c92de7ad628b0bd717faf2b6",
    "app/static/vendor/xterm/xterm-addon-fit-0.10.0.js": "3e9ae6f6965ee1269d5574b905cf1bf559c2f44c784255b0034a71387e7719c9",
}


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


def test_vendored_xterm_matches_the_reviewed_bytes() -> None:
    """The live tools terminal must run only reviewed, vendored xterm bytes."""
    for rel_path, expected in XTERM_ASSETS.items():
        asset = REPOSITORY_ROOT / rel_path
        assert asset.exists(), f"missing vendored asset {rel_path}"
        assert hashlib.sha256(asset.read_bytes()).hexdigest() == expected, rel_path


def test_live_tools_template_uses_vendored_xterm() -> None:
    """The live tools page must not pull xterm from a remote origin."""
    template = (REPOSITORY_ROOT / "app/templates/rmm/live_tools.html").read_text()
    assert "static/vendor/xterm/xterm-5.5.0.js" in template
    assert "cdn" not in template.lower()


def test_jinja_templates_do_not_load_remote_scripts() -> None:
    """Keep third-party JavaScript out of authenticated and secret-bearing views."""
    template_root = REPOSITORY_ROOT / "app/templates"
    offenders = [
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in template_root.rglob("*.html")
        if REMOTE_SCRIPT.search(path.read_text())
    ]

    assert offenders == []
