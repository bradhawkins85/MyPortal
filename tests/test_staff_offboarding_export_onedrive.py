import pytest
from unittest.mock import AsyncMock

from app.services import staff_onboarding_workflows as workflows


@pytest.mark.anyio
async def test_export_onedrive_creates_upn_folder_copies_children_and_marks_read_only(monkeypatch):
    calls = {"get": [], "get_all": [], "post_location": [], "post": []}

    monkeypatch.setattr(
        workflows.m365_service,
        "acquire_access_token",
        AsyncMock(return_value="token"),
    )
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(return_value={"id": "user-id", "userPrincipalName": "user@example.com"}),
    )

    async def fake_graph_get(token, url):
        calls["get"].append((token, url))
        return {"id": "root-id", "name": "root"}

    async def fake_graph_get_all(token, url):
        calls["get_all"].append((token, url))
        return [
            {"id": "child-1", "name": "Documents"},
            {"id": "child-2", "name": "Notes.txt"},
        ]

    async def fake_graph_post(token, url, payload):
        calls["post"].append((token, url, payload))
        if url.endswith("/children"):
            return {"id": "dest-folder-id", "name": payload["name"], "webUrl": "https://sharepoint/export"}
        return {"value": []}

    async def fake_post_location(token, url, payload):
        calls["post_location"].append((token, url, payload))
        return {}, f"https://graph.microsoft.com/monitor/{len(calls['post_location'])}"

    async def fake_wait(token, monitor_url, *, timeout_seconds):
        return {"status": "completed"}

    monkeypatch.setattr(workflows.m365_service, "_graph_get", fake_graph_get)
    monkeypatch.setattr(workflows.m365_service, "_graph_get_all", fake_graph_get_all)
    monkeypatch.setattr(workflows.m365_service, "_graph_post", fake_graph_post)
    monkeypatch.setattr(workflows, "_graph_post_for_location", fake_post_location)
    monkeypatch.setattr(workflows, "_wait_for_graph_copy", fake_wait)

    result = await workflows._run_export_onedrive_step(
        company_id=42,
        staff={"id": 7, "email": "user@example.com"},
        step_config={
            "destination_drive_id": "drive-id",
            "destination_parent_item_id": "parent-id",
            "mark_source_read_only": True,
            "wait_for_completion": True,
            "folder_conflict_behavior": "fail",
        },
        vars_map={},
    )

    assert result["destination_folder_name"] == "user@example.com"
    assert result["destination_folder_id"] == "dest-folder-id"
    assert result["copy_status"] == "completed"
    assert result["source_items_submitted"] == 2
    assert result["source_marked_read_only"] is True
    assert calls["post"][0][2] == {
        "name": "user@example.com",
        "folder": {},
        "@microsoft.graph.conflictBehavior": "fail",
    }
    assert calls["post_location"][0][2] == {
        "parentReference": {"driveId": "drive-id", "id": "dest-folder-id"},
        "name": "Documents",
    }
    assert calls["post_location"][1][2] == {
        "parentReference": {"driveId": "drive-id", "id": "dest-folder-id"},
        "name": "Notes.txt",
    }
    assert calls["post"][-1][2]["roles"] == ["read"]
    assert calls["post"][-1][2]["retainInheritedPermissions"] is False


@pytest.mark.anyio
async def test_export_onedrive_uses_drive_root_children_endpoint_for_default_parent(monkeypatch):
    calls = {"get": [], "get_all": [], "post_location": [], "post": []}

    monkeypatch.setattr(
        workflows.m365_service,
        "acquire_access_token",
        AsyncMock(return_value="token"),
    )
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(return_value={"id": "user-id", "userPrincipalName": "user@example.com"}),
    )

    async def fake_graph_get(token, url):
        calls["get"].append((token, url))
        return {"id": "root-id", "name": "root"}

    async def fake_graph_get_all(token, url):
        calls["get_all"].append((token, url))
        return []

    async def fake_graph_post(token, url, payload):
        calls["post"].append((token, url, payload))
        if url.endswith("/children"):
            return {"id": "dest-folder-id", "name": payload["name"]}
        return {"value": []}

    monkeypatch.setattr(workflows.m365_service, "_graph_get", fake_graph_get)
    monkeypatch.setattr(workflows.m365_service, "_graph_get_all", fake_graph_get_all)
    monkeypatch.setattr(workflows.m365_service, "_graph_post", fake_graph_post)

    result = await workflows._run_export_onedrive_step(
        company_id=42,
        staff={"id": 7, "email": "user@example.com"},
        step_config={
            "destination_drive_id": "drive-id",
            "destination_parent_item_id": "root",
            "mark_source_read_only": False,
        },
        vars_map={},
    )

    assert result["destination_folder_id"] == "dest-folder-id"
    assert calls["post"][0][1] == "https://graph.microsoft.com/v1.0/drives/drive-id/root/children"
    assert "/items/root/children" not in calls["post"][0][1]


@pytest.mark.anyio
async def test_export_onedrive_requires_destination_drive_id(monkeypatch):
    monkeypatch.setattr(workflows.company_repo, "get_company_by_id", AsyncMock(return_value={}))
    with pytest.raises(workflows.WorkflowStepError, match="destination_drive_id"):
        await workflows._run_export_onedrive_step(
            company_id=42,
            staff={"id": 7, "email": "user@example.com"},
            step_config={},
            vars_map={},
        )


@pytest.mark.anyio
async def test_export_onedrive_destination_403_raises_actionable_permission_error(monkeypatch):
    monkeypatch.setattr(
        workflows.m365_service,
        "acquire_access_token",
        AsyncMock(return_value="token"),
    )
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(return_value={"id": "user-id", "userPrincipalName": "user@example.com"}),
    )
    monkeypatch.setattr(
        workflows.m365_service,
        "_graph_get",
        AsyncMock(return_value={"id": "root-id", "name": "root"}),
    )

    async def forbidden_post(token, url, payload):
        raise workflows.M365Error(
            "Microsoft Graph POST failed (403): Access denied",
            http_status=403,
            graph_error_code="accessDenied",
        )

    monkeypatch.setattr(workflows.m365_service, "_graph_post", forbidden_post)

    with pytest.raises(workflows.WorkflowStepError) as exc_info:
        await workflows._run_export_onedrive_step(
            company_id=42,
            staff={"id": 7, "email": "user@example.com"},
            step_config={"destination_drive_id": "drive-id"},
            vars_map={},
        )

    assert exc_info.value.http_status == 403
    assert "Sites.ReadWrite.All" in str(exc_info.value)
    assert "Sites.Selected" in str(exc_info.value)
    assert exc_info.value.request_payload == {"operation": "create_destination_folder"}
