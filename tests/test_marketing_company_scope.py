"""Marketing is limited to the companies where the user has marketing access."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.features.marketing import campaign_routes
from app.features.marketing import routes as marketing_routes
from app.repositories import marketing_campaigns as campaign_repo
from app.services import marketing_campaigns as campaigns

COMPANY_A = 1
COMPANY_B = 2
USER = {"id": 5, "email": "tech@a.example", "is_super_admin": False}
SUPER_ADMIN = {"id": 1, "email": "admin@msp.example", "is_super_admin": True}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(campaigns, "_portal_url", lambda: "https://portal.example.com")
    monkeypatch.setattr(campaigns, "default_sender", lambda: "news@msp.example")
    monkeypatch.setattr(
        campaign_repo,
        "find_staff_by_emails",
        AsyncMock(
            side_effect=lambda emails: [
                row
                for row in (
                    {"staff_id": 10, "company_id": COMPANY_A, "email": "jo@a.example"},
                    {"staff_id": 20, "company_id": COMPANY_B, "email": "sam@b.example"},
                )
                if row["email"] in emails
            ]
        ),
    )


def _selected(*company_ids: int, **extra) -> dict:
    return {"company_mode": "selected", "company_ids": list(company_ids), **extra}


def _campaign(**overrides) -> dict:
    campaign = {
        "id": 7,
        "name": "Renewal",
        "category": "updates",
        "subject": "Hello",
        "body_html": "<p>Hi</p>",
        "sender_email": None,
        "audience": _selected(COMPANY_A),
        "status": "draft",
    }
    campaign.update(overrides)
    return campaign


def _membership(company_id: int, *permissions: str) -> dict:
    return {"company_id": company_id, "legacy_permissions": list(permissions)}


def _request(form: dict | None = None) -> Request:
    body = urlencode(form or {}, doseq=True).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
            "query_string": b"",
        },
        receive,
    )


# ---------------------------------------------------------------------------
# Which companies a user may market to
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_scope_is_only_companies_whose_role_grants_marketing(monkeypatch):
    monkeypatch.setattr(
        marketing_routes.membership_repo,
        "list_memberships_for_user",
        AsyncMock(return_value=[_membership(COMPANY_A, "marketing.access"), _membership(COMPANY_B, "tickets.view")]),
    )
    monkeypatch.setattr(marketing_routes.user_permissions_repo, "list_user_permissions", AsyncMock(return_value=[]))

    assert await marketing_routes._marketing_company_ids(USER) == {COMPANY_A}


@pytest.mark.anyio
async def test_scope_includes_direct_company_grants(monkeypatch):
    monkeypatch.setattr(
        marketing_routes.membership_repo,
        "list_memberships_for_user",
        AsyncMock(return_value=[_membership(COMPANY_B)]),
    )
    monkeypatch.setattr(
        marketing_routes.user_permissions_repo, "list_user_permissions", AsyncMock(return_value=["marketing.access"])
    )

    assert await marketing_routes._marketing_company_ids(USER) == {COMPANY_B}


@pytest.mark.anyio
async def test_switch_all_technician_gets_every_company_only_with_marketing(monkeypatch):
    monkeypatch.setattr(marketing_routes.user_permissions_repo, "list_user_permissions", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        marketing_routes.company_repo, "list_companies", AsyncMock(return_value=[{"id": 1}, {"id": 2}, {"id": 3}])
    )
    memberships = AsyncMock(return_value=[_membership(COMPANY_A, "company.switch_all", "marketing.access")])
    monkeypatch.setattr(marketing_routes.membership_repo, "list_memberships_for_user", memberships)
    assert await marketing_routes._marketing_company_ids(USER) == {1, 2, 3}

    memberships.return_value = [
        _membership(COMPANY_A, "company.switch_all"),
        _membership(COMPANY_B, "marketing.access"),
    ]
    assert await marketing_routes._marketing_company_ids(USER) == {COMPANY_B}


@pytest.mark.anyio
async def test_super_admin_scope_is_unrestricted():
    assert await marketing_routes._marketing_company_ids(SUPER_ADMIN) is None


# ---------------------------------------------------------------------------
# Audience and sender rules
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_scoped_user_cannot_target_other_companies():
    scope = {COMPANY_A}
    assert await campaigns.audience_scope_error(_selected(COMPANY_A), scope) is None
    assert await campaigns.audience_scope_error(_selected(COMPANY_A, COMPANY_B), scope)
    assert await campaigns.audience_scope_error({"company_mode": "all"}, scope)
    assert await campaigns.audience_scope_error(_selected(COMPANY_A, exclude_company_ids=[COMPANY_B]), scope)
    assert await campaigns.audience_scope_error(_selected(COMPANY_A, include_emails=["sam@b.example"]), scope)
    assert await campaigns.audience_scope_error(_selected(COMPANY_A, include_emails=["x@outside.example"]), scope)
    assert await campaigns.audience_scope_error(_selected(COMPANY_A, include_emails=["jo@a.example"]), scope) is None


@pytest.mark.anyio
async def test_super_admin_can_target_any_company_and_sender():
    assert await campaigns.audience_scope_error({"company_mode": "all"}, None) is None
    assert await campaigns.audience_scope_error(_selected(COMPANY_B, include_emails=["x@y.example"]), None) is None
    assert campaigns.sender_scope_error("ceo@anything.example", None) is None


def test_scoped_user_can_only_use_default_sender():
    assert campaigns.sender_scope_error(None, {COMPANY_A}) is None
    assert campaigns.sender_scope_error("News@MSP.example", {COMPANY_A}) is None
    assert campaigns.sender_scope_error("ceo@msp.example", {COMPANY_A})


@pytest.mark.anyio
async def test_resolve_audience_drops_contacts_outside_scope(monkeypatch):
    monkeypatch.setattr(
        campaign_repo,
        "find_audience_contacts",
        AsyncMock(
            return_value=[
                {"staff_id": 10, "company_id": COMPANY_A, "email": "jo@a.example"},
                {"staff_id": 20, "company_id": COMPANY_B, "email": "sam@b.example"},
            ]
        ),
    )
    from app.repositories import email_blocklist

    monkeypatch.setattr(email_blocklist, "filter_allowed", AsyncMock(return_value=([], [])))

    scoped = await campaigns.resolve_audience(_campaign(), company_ids={COMPANY_A})
    assert [r["email"] for r in scoped["recipients"]] == ["jo@a.example"]
    everyone = await campaigns.resolve_audience(_campaign())
    assert [r["email"] for r in everyone["recipients"]] == ["jo@a.example", "sam@b.example"]


@pytest.mark.anyio
async def test_queue_refuses_campaign_outside_scope(monkeypatch):
    monkeypatch.setattr(
        campaign_repo, "get_campaign", AsyncMock(return_value=_campaign(audience=_selected(COMPANY_B)))
    )
    mark = AsyncMock(return_value=True)
    monkeypatch.setattr(campaign_repo, "mark_campaign_sending", mark)

    with pytest.raises(campaigns.CampaignError):
        await campaigns.queue_campaign(7, company_ids={COMPANY_A})
    mark.assert_not_awaited()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _patch_routes(monkeypatch, user: dict, scope: set[int] | None) -> dict:
    rendered: dict = {}

    async def render(template, request, current_user, *, extra=None):
        rendered["template"] = template
        rendered["extra"] = extra
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(campaign_routes, "_require_marketing_scope", AsyncMock(return_value=(user, scope, None)))
    monkeypatch.setattr(campaign_routes, "_main", lambda: SimpleNamespace(_render_template=render))
    monkeypatch.setattr(campaign_routes.audit_service, "record", AsyncMock())
    return rendered


@pytest.mark.anyio
async def test_campaign_for_other_company_is_hidden_from_scoped_user(monkeypatch):
    rendered = _patch_routes(monkeypatch, USER, {COMPANY_A})
    own = _campaign(id=1)
    other = _campaign(id=2, audience=_selected(COMPANY_B))
    everyone = _campaign(id=3, audience={"company_mode": "all"})
    monkeypatch.setattr(campaign_repo, "list_campaigns", AsyncMock(return_value=[own, other, everyone]))
    opt_outs = AsyncMock(return_value=[])
    monkeypatch.setattr(campaign_repo, "list_opt_outs", opt_outs)

    await campaign_routes.admin_marketing_campaigns(_request())
    assert [c["id"] for c in rendered["extra"]["campaigns"]] == [1]
    assert opt_outs.await_args.kwargs["company_ids"] == {COMPANY_A}

    monkeypatch.setattr(campaign_repo, "get_campaign", AsyncMock(return_value=other))
    for handler in (
        campaign_routes.admin_marketing_campaign_detail,
        campaign_routes.admin_marketing_edit_campaign,
        campaign_routes.admin_marketing_campaign_preview,
        campaign_routes.admin_marketing_campaign_send,
        campaign_routes.admin_marketing_campaign_cancel,
        campaign_routes.admin_marketing_campaign_delete,
    ):
        with pytest.raises(HTTPException) as exc:
            await handler(2, _request())
        assert exc.value.status_code == 404, handler.__name__


@pytest.mark.anyio
async def test_super_admin_sees_every_campaign(monkeypatch):
    rendered = _patch_routes(monkeypatch, SUPER_ADMIN, None)
    campaigns_list = [_campaign(id=1), _campaign(id=2, audience=_selected(COMPANY_B)), _campaign(id=3, audience={})]
    monkeypatch.setattr(campaign_repo, "list_campaigns", AsyncMock(return_value=campaigns_list))
    monkeypatch.setattr(campaign_repo, "list_opt_outs", AsyncMock(return_value=[]))

    await campaign_routes.admin_marketing_campaigns(_request())
    assert [c["id"] for c in rendered["extra"]["campaigns"]] == [1, 2, 3]


@pytest.mark.anyio
async def test_scoped_user_cannot_save_campaign_for_other_company(monkeypatch):
    rendered = _patch_routes(monkeypatch, USER, {COMPANY_A})
    monkeypatch.setattr(campaign_repo, "list_company_options", AsyncMock(return_value=[]))
    monkeypatch.setattr(campaign_repo, "list_asset_field_options", AsyncMock(return_value=[]))
    monkeypatch.setattr(campaign_repo, "list_product_options", AsyncMock(return_value=[]))
    monkeypatch.setattr(campaign_routes.message_templates_service, "list_templates", AsyncMock(return_value=[]))
    create = AsyncMock(return_value=99)
    monkeypatch.setattr(campaign_repo, "create_campaign", create)
    base = {"name": "Promo", "subject": "Hi", "body_html": "<p>Hi</p>", "company_mode": "selected"}

    response = await campaign_routes.admin_marketing_create_campaign(_request({**base, "company_ids": [str(COMPANY_B)]}))
    assert response.status_code == 403
    assert rendered["extra"]["can_target_all_companies"] is False
    campaign_repo.list_company_options.assert_awaited_with({COMPANY_A})

    response = await campaign_routes.admin_marketing_create_campaign(
        _request({**base, "company_ids": [str(COMPANY_A)], "sender_email": "ceo@msp.example"})
    )
    assert response.status_code == 403
    create.assert_not_awaited()

    await campaign_routes.admin_marketing_create_campaign(_request({**base, "company_ids": [str(COMPANY_A)]}))
    create.assert_awaited_once()


@pytest.mark.anyio
async def test_scoped_user_cannot_clear_other_company_opt_out(monkeypatch):
    _patch_routes(monkeypatch, USER, {COMPANY_A})
    remove = AsyncMock()
    monkeypatch.setattr(campaign_repo, "remove_opt_out", remove)

    with pytest.raises(HTTPException) as exc:
        await campaign_routes.admin_marketing_remove_opt_out(_request({"email": "sam@b.example"}))
    assert exc.value.status_code == 404
    remove.assert_not_awaited()

    await campaign_routes.admin_marketing_remove_opt_out(_request({"email": "jo@a.example"}))
    remove.assert_awaited_once()
