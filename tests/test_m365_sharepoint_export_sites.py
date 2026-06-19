import pytest

from app.services import m365 as m365_service


@pytest.mark.anyio
async def test_list_sharepoint_export_sites_includes_default_drive(monkeypatch):
    async def fake_acquire(company_id, force_client_credentials=False):
        assert company_id == 42
        assert force_client_credentials is True
        return "token"

    async def fake_get_all(token, url):
        assert token == "token"
        assert "sites?search=*" in url
        return [
            {"id": "site-2", "displayName": "Beta", "webUrl": "https://contoso/sites/beta"},
            {"id": "site-1", "displayName": "Alpha", "webUrl": "https://contoso/sites/alpha"},
        ]

    async def fake_get(token, url, *, extra_headers=None):
        if "site-1" in url:
            return {"id": "drive-1", "name": "Documents", "webUrl": "https://contoso/sites/alpha/docs"}
        return {"id": "drive-2", "name": "Shared Documents", "webUrl": "https://contoso/sites/beta/docs"}

    monkeypatch.setattr(m365_service, "acquire_access_token", fake_acquire)
    monkeypatch.setattr(m365_service, "_graph_get_all", fake_get_all)
    monkeypatch.setattr(m365_service, "_graph_get", fake_get)

    sites = await m365_service.list_sharepoint_export_sites(42)

    assert [site["site_name"] for site in sites] == ["Alpha", "Beta"]
    assert sites[0]["drive_id"] == "drive-1"
    assert sites[0]["label"] == "Alpha (Documents)"


@pytest.mark.anyio
async def test_list_sharepoint_export_sites_403_mentions_sites_read_all(monkeypatch):
    async def fake_acquire(company_id, force_client_credentials=False):
        return "token"

    async def fake_get_all(token, url):
        raise m365_service.M365Error("Microsoft Graph request failed (403)", http_status=403)

    monkeypatch.setattr(m365_service, "acquire_access_token", fake_acquire)
    monkeypatch.setattr(m365_service, "_graph_get_all", fake_get_all)

    with pytest.raises(m365_service.M365Error) as exc_info:
        await m365_service.list_sharepoint_export_sites(42)

    assert exc_info.value.http_status == 403
    assert "Sites.Read.All" in str(exc_info.value)
    assert "reconnect the company" in str(exc_info.value)


def test_provision_app_roles_includes_sites_read_all_for_sharepoint_export_picker():
    assert m365_service._SITES_READ_ALL_ROLE in m365_service._PROVISION_APP_ROLES
    assert m365_service._GRAPH_ROLE_NAMES[m365_service._SITES_READ_ALL_ROLE] == "Sites.Read.All"
