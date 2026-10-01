from __future__ import annotations

import pytest

from app.security.flash import flash_redirect


def test_flash_redirect_allows_local_path() -> None:
    response = flash_redirect("/admin/foo", "Saved successfully.", "success")

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/foo"
    assert "_flash=" in response.headers.get("set-cookie", "")


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/phish",
        "//evil.example/phish",
        "admin/foo",
        "/%2F/evil.example/phish",
        "/%5C%5Cevil.example/phish",
        "",
        "   ",
        None,
    ],
)
def test_flash_redirect_rejects_non_local_targets(target: str | None) -> None:
    response = flash_redirect(target, "Saved successfully.", "success")

    assert response.headers["location"] == "/"


def test_flash_redirect_normalizes_backslashes() -> None:
    response = flash_redirect("\\admin\\foo", "Saved successfully.", "success")

    assert response.headers["location"] == "/admin/foo"


def test_safe_redirect_target_rejects_backslash_and_control_char_tricks():
    from app.security.flash import _safe_redirect_target

    for bad in ("/\\evil.example", "\\\\evil.example", "/\t/evil.example", "/\n/evil.example"):
        assert _safe_redirect_target(bad, fallback="/home") == "/home"
    assert _safe_redirect_target("/tickets/1?x=1", fallback="/home") == "/tickets/1?x=1"


def test_admin_ticket_redirect_helper_uses_shared_logic():
    from app.features.tickets.admin_routes import _safe_local_redirect_target

    assert _safe_local_redirect_target("/\\evil.example", fallback="") == ""
    assert _safe_local_redirect_target("/admin/tickets/5", fallback="") == "/admin/tickets/5"
