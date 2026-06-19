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
