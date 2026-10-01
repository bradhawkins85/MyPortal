"""Tests for safely provisioning documented M365 emergency administrators."""

import asyncio
from unittest.mock import AsyncMock, patch

from app.services import m365_best_practices as service


def test_global_admin_remediation_creates_three_separate_hudu_passwords():
    async def run():
        posts = []

        async def graph_get(_token, url):
            if "directoryRoles" in url:
                return {"value": [{"id": "role-1"}]}
            return {
                "value": [{"id": "example.com", "isDefault": True, "isVerified": True}]
            }

        async def graph_post(_token, url, payload):
            posts.append((url, payload))
            if url.endswith("/users"):
                user_count = len([p for p in posts if p[0].endswith("/users")])
                return {"id": f"user-{user_count}"}
            return {"id": f"assignment-{len(posts)}"}

        hudu_create = AsyncMock(return_value={"id": "password-1"})
        with (
            patch.object(
                service.companies_repo,
                "get_company_by_id",
                new=AsyncMock(return_value={"hudu_id": "42"}),
            ),
            patch.object(
                service.hudu_service, "validate_configuration", new=AsyncMock()
            ),
            patch.object(
                service.hudu_service, "create_asset_password", new=hudu_create
            ),
            patch.object(service, "_graph_get", side_effect=graph_get),
            patch.object(service, "_graph_get_all", new=AsyncMock(return_value=[])),
            patch.object(service, "_graph_post", side_effect=graph_post),
            patch.object(
                service,
                "_post_app_role_assignment_with_retry",
                side_effect=graph_post,
            ),
        ):
            success, message = await service._remediate_global_admin_count("token", 7)

        assert success is True
        assert "Created 3" in message
        assert hudu_create.await_count == 3
        credentials = [call.kwargs for call in hudu_create.await_args_list]
        assert credentials[0]["username"] != credentials[1]["username"]
        assert credentials[0]["password"] != credentials[1]["password"]
        assert all(len(item["password"]) == 32 for item in credentials)
        assert len([url for url, _ in posts if url.endswith("/users")]) == 3
        assert len([url for url, _ in posts if url.endswith("/roleAssignments")]) == 3

    asyncio.run(run())


def test_global_admin_remediation_adds_one_when_two_exist():
    async def run():
        graph_post = AsyncMock(return_value={"id": "created-id"})
        hudu_create = AsyncMock(return_value={"id": "password-1"})
        with (
            patch.object(
                service.companies_repo,
                "get_company_by_id",
                new=AsyncMock(return_value={"hudu_id": "42"}),
            ),
            patch.object(
                service.hudu_service, "validate_configuration", new=AsyncMock()
            ),
            patch.object(
                service.hudu_service, "create_asset_password", new=hudu_create
            ),
            patch.object(
                service,
                "_graph_get",
                side_effect=[
                    {"value": [{"id": "role-1"}]},
                    {
                        "value": [
                            {
                                "id": "example.com",
                                "isDefault": True,
                                "isVerified": True,
                            }
                        ]
                    },
                ],
            ),
            patch.object(
                service,
                "_graph_get_all",
                new=AsyncMock(
                    return_value=[{"id": "admin-1"}, {"id": "admin-2"}]
                ),
            ),
            patch.object(service, "_graph_post", new=graph_post),
            patch.object(
                service,
                "_post_app_role_assignment_with_retry",
                new=AsyncMock(return_value={"id": "assignment-1"}),
            ),
        ):
            success, message = await service._remediate_global_admin_count("token", 7)

        assert success is True
        assert "Created 1" in message
        assert hudu_create.await_count == 1

    asyncio.run(run())


def test_global_admin_remediation_requires_hudu_before_graph_writes():
    async def run():
        with (
            patch.object(
                service.companies_repo,
                "get_company_by_id",
                new=AsyncMock(return_value={"hudu_id": None}),
            ),
            patch.object(service, "_graph_post", new=AsyncMock()) as graph_post,
        ):
            success, message = await service._remediate_global_admin_count("token", 7)

        assert success is False
        assert "Hudu ID" in message
        graph_post.assert_not_awaited()

    asyncio.run(run())


def test_generated_admin_password_has_required_character_classes():
    password = service._generate_emergency_admin_password()
    assert len(password) == 32
    assert any(char.isupper() for char in password)
    assert any(char.islower() for char in password)
    assert any(char.isdigit() for char in password)
    assert any(char in "!@#$%^&*-_=+" for char in password)


def test_global_admin_remediation_preserves_original_failure_when_cleanup_fails():
    async def run():
        delete_calls = []

        async def graph_get(_token, url):
            if "directoryRoles" in url:
                return {"value": [{"id": "role-1"}]}
            return {
                "value": [{"id": "example.com", "isDefault": True, "isVerified": True}]
            }

        async def graph_post(_token, url, payload):
            if url.endswith("/users"):
                return {"id": "user-1"}
            return {"id": "assignment-1"}

        async def graph_delete(_token, url):
            delete_calls.append(url)
            raise RuntimeError("cleanup failed")

        with (
            patch.object(
                service.companies_repo,
                "get_company_by_id",
                new=AsyncMock(return_value={"hudu_id": "42"}),
            ),
            patch.object(service.hudu_service, "validate_configuration", new=AsyncMock()),
            patch.object(
                service.hudu_service,
                "create_asset_password",
                new=AsyncMock(side_effect=RuntimeError("primary failure")),
            ),
            patch.object(service, "_graph_get", side_effect=graph_get),
            patch.object(service, "_graph_get_all", new=AsyncMock(return_value=[])),
            patch.object(service, "_graph_post", side_effect=graph_post),
            patch.object(
                service,
                "_post_app_role_assignment_with_retry",
                new=AsyncMock(return_value={"id": "assignment-1"}),
            ),
            patch.object(service, "_graph_delete", side_effect=graph_delete),
            patch.object(service, "log_error") as log_error,
        ):
            try:
                await service._remediate_global_admin_count("token", 7)
            except RuntimeError as exc:
                assert str(exc) == "primary failure"
            else:
                raise AssertionError("Expected remediation to re-raise the primary failure")

        assert len(delete_calls) == 2
        assert log_error.call_count == 2

    asyncio.run(run())
