import pytest

from app.services import m365_signature_deployment as deployment


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _async(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


@pytest.fixture
def exchange(monkeypatch):
    calls = []

    async def fake_mailboxes(company_id):
        return [
            {"user_principal_name": "Ada@example.com", "display_name": "Ada"},
            {"user_principal_name": "grace@example.com", "display_name": "Grace"},
            {"user_principal_name": "shared@example.com", "display_name": "No staff"},
        ]

    async def fake_staff(company_id, email):
        return {
            "ada@example.com": {"id": 1, "first_name": "Ada", "last_name": "Lovelace"},
            "grace@example.com": {"id": 2, "first_name": "Grace", "last_name": "Hopper"},
        }.get(email.casefold())

    async def fake_render(company_id, *, html_content, text_content, staff_id):
        return {
            "html": f"<p>{html_content}:{staff_id}</p>",
            "text": f"{text_content}:{staff_id}",
            "missing_tokens": ["staff.mobile_phone"] if staff_id == 2 else [],
        }

    async def fake_invoke(token, tenant_id, cmdlet, parameters=None):
        calls.append((token, tenant_id, cmdlet, parameters))
        if parameters and parameters.get("Identity") == "grace@example.com":
            raise deployment.m365_service.M365Error("Mailbox not found")
        return {}

    monkeypatch.setattr(deployment.oof_service, "get_selectable_mailboxes", fake_mailboxes)
    monkeypatch.setattr(deployment.staff_repo, "get_staff_by_company_and_email", fake_staff)
    monkeypatch.setattr(deployment.signatures_service, "render_preview", fake_render)
    monkeypatch.setattr(
        deployment.signatures_service,
        "get_template",
        _async({"id": 5, "status": "published", "html_content": "H", "text_content": "T"}),
    )
    monkeypatch.setattr(
        deployment.m365_service, "_acquire_exo_access_token", _async(("exo-token", "tenant"))
    )
    monkeypatch.setattr(deployment.m365_service, "_exo_invoke_command", fake_invoke)
    return calls


@pytest.mark.anyio
async def test_targets_report_matching_staff_records(exchange):
    targets = await deployment.list_deployment_targets(7)
    assert [(t["user_principal_name"], t["staff_id"], t["staff_label"]) for t in targets] == [
        ("Ada@example.com", 1, "Ada Lovelace"),
        ("grace@example.com", 2, "Grace Hopper"),
        ("shared@example.com", None, None),
    ]


@pytest.mark.anyio
async def test_deploy_renders_per_mailbox_and_keeps_partial_failures(exchange):
    results = await deployment.deploy_template(
        7, 5, ["ada@example.com", "ADA@example.com", "grace@example.com"], auto_add_reply=False
    )

    assert results == [
        {"mailbox": "Ada@example.com", "success": True, "error": None, "missing_tokens": []},
        {"mailbox": "grace@example.com", "success": False, "error": "Mailbox not found", "missing_tokens": []},
    ]
    token, tenant, cmdlet, params = exchange[0]
    assert (token, tenant, cmdlet) == ("exo-token", "tenant", "Set-MailboxMessageConfiguration")
    assert params == {
        "Identity": "Ada@example.com",
        "SignatureHtml": "<p>H:1</p>",
        "SignatureText": "T:1",
        "SignatureTextOnMobile": "T:1",
        "AutoAddSignature": True,
        "AutoAddSignatureOnReply": False,
    }


@pytest.mark.anyio
async def test_deploy_rejects_unknown_unmatched_and_unpublished(exchange, monkeypatch):
    with pytest.raises(ValueError, match="Unknown user mailbox"):
        await deployment.deploy_template(7, 5, ["other@example.com"])
    with pytest.raises(ValueError, match="No staff record"):
        await deployment.deploy_template(7, 5, ["shared@example.com"])
    with pytest.raises(ValueError, match="Select at least one"):
        await deployment.deploy_template(7, 5, [])
    monkeypatch.setattr(
        deployment.signatures_service, "get_template", _async({"id": 5, "status": "draft"})
    )
    with pytest.raises(ValueError, match="Publish"):
        await deployment.deploy_template(7, 5, ["ada@example.com"])
    assert exchange == []


@pytest.mark.anyio
async def test_roaming_status_and_postpone(exchange, monkeypatch):
    async def fake_invoke(token, tenant_id, cmdlet, parameters=None):
        exchange.append((cmdlet, parameters))
        return {"value": [{"PostponeRoamingSignaturesUntilLater": False}]}

    monkeypatch.setattr(deployment.m365_service, "_exo_invoke_command", fake_invoke)
    assert await deployment.get_roaming_signature_status(7) == {"postponed": False, "error": None}
    await deployment.postpone_roaming_signatures(7)
    assert exchange[-1] == ("Set-OrganizationConfig", {"PostponeRoamingSignaturesUntilLater": True})

    async def failing(*args, **kwargs):
        raise deployment.m365_service.M365Error("forbidden")

    monkeypatch.setattr(deployment.m365_service, "_exo_invoke_command", failing)
    assert await deployment.get_roaming_signature_status(7) == {"postponed": None, "error": "forbidden"}


@pytest.mark.anyio
async def test_classic_outlook_renders_only_company_staff_addresses(exchange, monkeypatch):
    monkeypatch.setattr(
        deployment.companies_repo, "get_email_domains_for_company", _async(["example.com"])
    )
    monkeypatch.setattr(
        deployment.signatures_service,
        "get_primary_template",
        _async({"id": 9, "slug": "standard", "html_content": "H", "text_content": "T"}),
    )

    async def fake_staff(company_id, email):
        return {
            "ada@example.com": {"id": 1, "enabled": True, "is_ex_staff": False},
            "former@example.com": {"id": 3, "enabled": True, "is_ex_staff": True},
        }.get(email.casefold())

    monkeypatch.setattr(deployment.staff_repo, "get_staff_by_company_and_email", fake_staff)

    result = await deployment.render_classic_outlook_signatures(
        7, ["Ada@Example.com", "ada@example.com", "someone@other.com", "former@example.com", "nobody@example.com"]
    )

    assert result["template_slug"] == "standard"
    assert [(s["address"], s["name"], s["html"], s["text"]) for s in result["signatures"]] == [
        ("Ada@Example.com", "MyPortal (Ada@Example.com)", "<p>H:1</p>", "T:1")
    ]
    assert len(result["signatures"][0]["hash"]) == 64
    assert set(result["skipped"]) == {"someone@other.com", "former@example.com", "nobody@example.com"}


@pytest.mark.anyio
async def test_classic_outlook_without_active_template_skips_everything(exchange, monkeypatch):
    monkeypatch.setattr(
        deployment.companies_repo, "get_email_domains_for_company", _async(["example.com"])
    )
    monkeypatch.setattr(deployment.signatures_service, "get_primary_template", _async(None))

    result = await deployment.render_classic_outlook_signatures(7, ["ada@example.com"])

    assert result == {
        "template_slug": None,
        "signatures": [],
        "skipped": {"ada@example.com": "No active signature template"},
    }


@pytest.mark.anyio
async def test_tray_endpoint_requires_company_opt_in(monkeypatch):
    from app.api.routes import tray as tray_routes
    from app.schemas.tray import TrayOutlookSignaturesRequest

    companies = {1: {"id": 1, "classic_outlook_signatures_enabled": 0}, 2: {"id": 2, "classic_outlook_signatures_enabled": 1}}
    monkeypatch.setattr(
        tray_routes.companies_repo, "get_company_by_id", lambda company_id: _async(companies.get(company_id))()
    )
    calls = []

    async def fake_render(company_id, addresses):
        calls.append((company_id, addresses))
        return {"template_slug": "standard", "signatures": [], "skipped": {}}

    monkeypatch.setattr(
        tray_routes.signature_deploy_service, "render_classic_outlook_signatures", fake_render
    )
    payload = TrayOutlookSignaturesRequest(addresses=["ada@example.com"])

    disabled = await tray_routes.get_outlook_signatures(payload, device={"company_id": 1})
    unassigned = await tray_routes.get_outlook_signatures(payload, device={"company_id": None})
    enabled = await tray_routes.get_outlook_signatures(payload, device={"company_id": 2})

    assert disabled.enabled is False and unassigned.enabled is False
    assert enabled.enabled is True and enabled.template_slug == "standard"
    assert calls == [(2, ["ada@example.com"])]
