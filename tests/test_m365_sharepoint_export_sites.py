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


def test_provision_app_roles_includes_sites_readwrite_all_for_onedrive_export_writes():
    assert m365_service._SITES_READWRITE_ALL_ROLE in m365_service._PROVISION_APP_ROLES
    assert (
        m365_service._GRAPH_ROLE_NAMES[m365_service._SITES_READWRITE_ALL_ROLE]
        == "Sites.ReadWrite.All"
    )
    graph_permissions = next(
        app["permissions"]
        for app in m365_service.ENTERPRISE_APP_CATALOG
        if app["app_id"] == m365_service._GRAPH_APP_ID
    )
    assert {permission["name"] for permission in graph_permissions} >= {
        "Sites.Read.All",
        "Sites.ReadWrite.All",
    }


@pytest.mark.anyio
async def test_create_offboarded_staff_export_site_returns_existing_site(monkeypatch):
    existing = {
        "site_id": "site-existing",
        "site_name": "Offboarded Staff",
        "drive_id": "drive-existing",
        "label": "Offboarded Staff (Documents)",
    }

    async def fake_list(company_id):
        assert company_id == 42
        return [existing]

    async def fail_acquire(*args, **kwargs):  # pragma: no cover - should not be called
        raise AssertionError("existing Offboarded Staff site should not create a new group")

    monkeypatch.setattr(m365_service, "list_sharepoint_export_sites", fake_list)
    monkeypatch.setattr(m365_service, "acquire_access_token", fail_acquire)

    result = await m365_service.create_offboarded_staff_export_site(42)

    assert result == {"status": "exists", "site": existing}


@pytest.mark.anyio
async def test_create_offboarded_staff_export_site_creates_group_and_returns_drive(monkeypatch):
    calls = []

    async def fake_list(company_id):
        return []

    async def fake_acquire(company_id, force_client_credentials=False):
        assert company_id == 42
        assert force_client_credentials is True
        return "token"

    async def fake_post(token, url, payload):
        calls.append((url, payload))
        assert token == "token"
        assert url == "https://graph.microsoft.com/v1.0/groups"
        assert payload["displayName"] == "Offboarded Staff"
        assert payload["mailNickname"] == "OffboardedStaff"
        assert payload["groupTypes"] == ["Unified"]
        return {"id": "group-1"}

    async def fake_get(token, url, *, extra_headers=None):
        assert token == "token"
        if "/groups/group-1/sites/root" in url:
            return {"id": "site-1", "displayName": "Offboarded Staff", "webUrl": "https://contoso/sites/offboardedstaff"}
        if "/sites/site-1/drive" in url:
            return {"id": "drive-1", "name": "Documents", "webUrl": "https://contoso/sites/offboardedstaff/docs"}
        raise AssertionError(f"unexpected Graph URL: {url}")

    async def fake_sleep(delay):
        raise AssertionError("site was ready immediately; sleep should not be called")

    monkeypatch.setattr(m365_service, "list_sharepoint_export_sites", fake_list)
    monkeypatch.setattr(m365_service, "acquire_access_token", fake_acquire)
    monkeypatch.setattr(m365_service, "_graph_post", fake_post)
    monkeypatch.setattr(m365_service, "_graph_get", fake_get)
    monkeypatch.setattr(m365_service.asyncio, "sleep", fake_sleep)

    result = await m365_service.create_offboarded_staff_export_site(42)

    assert result["status"] == "created"
    assert result["site"]["site_name"] == "Offboarded Staff"
    assert result["site"]["drive_id"] == "drive-1"
    assert len(calls) == 1


def test_provision_app_roles_includes_group_readwrite_all_for_offboarded_staff_site_creation():
    assert m365_service._GROUP_READWRITE_ALL_ROLE in m365_service._PROVISION_APP_ROLES
    assert m365_service._GRAPH_ROLE_NAMES[m365_service._GROUP_READWRITE_ALL_ROLE] == "Group.ReadWrite.All"
