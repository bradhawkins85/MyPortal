from unittest.mock import AsyncMock

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.repositories import chat as chat_repo


@pytest.mark.anyio
async def test_count_rooms_applies_user_company_and_open_filters(monkeypatch):
    fetch_one = AsyncMock(return_value={"count": 3})
    monkeypatch.setattr(chat_repo.db, "fetch_one", fetch_one)

    count = await chat_repo.count_rooms(company_id=7, user_id=42, status="open")

    assert count == 3
    sql, params = fetch_one.await_args.args
    assert "r.company_id = %s" in sql
    assert "chat_room_participants WHERE user_id = %s" in sql
    assert "r.status = %s" in sql
    assert params == (7, 42, "open")


def test_chat_menu_displays_open_chat_count_badge():
    env = Environment(
        loader=FileSystemLoader("app/templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.globals["static_url"] = lambda path: path
    template = env.get_template("base.html")

    html = template.render(
        request=type("Request", (), {"url": type("Url", (), {"path": "/"})()})(),
        current_user={"id": 42},
        active_membership={},
        menu_access={"menu.chat": "read"},
        matrix_chat_enabled=True,
        can_access_chat=True,
        available_companies=[],
        cart_summary={"item_count": 0, "total_quantity": 0, "subtotal": 0},
        notification_unread_count=0,
        chat_open_count=3,
        plausible_config={"enabled": False},
        csrf_token="csrf-token",
    )

    assert 'href="/chat"' in html
    assert '<span class="menu__badge">3</span>' in html


def test_chat_menu_hides_zero_open_chat_count_badge():
    env = Environment(
        loader=FileSystemLoader("app/templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.globals["static_url"] = lambda path: path
    template = env.get_template("base.html")

    html = template.render(
        request=type("Request", (), {"url": type("Url", (), {"path": "/"})()})(),
        current_user={"id": 42},
        active_membership={},
        menu_access={"menu.chat": "read"},
        matrix_chat_enabled=True,
        can_access_chat=True,
        available_companies=[],
        cart_summary={"item_count": 0, "total_quantity": 0, "subtotal": 0},
        notification_unread_count=0,
        chat_open_count=0,
        plausible_config={"enabled": False},
        csrf_token="csrf-token",
    )

    chat_link = html.split('href="/chat"', 1)[1].split("</a>", 1)[0]
    assert "menu__badge" not in chat_link
