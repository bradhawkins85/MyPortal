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
