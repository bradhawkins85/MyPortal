"""Only super administrators and MyPortal may change the files that link a
script folder to a company; every other script stays open to technicians."""

from __future__ import annotations

import json

import httpx
import pytest

from app.repositories import rmm as rmm_repo
from app.repositories import users as user_repo
from app.services import gitea, gitea_sign_in, rmm_scripts

SETTINGS = gitea.GiteaSettings(
    base_url="https://git.test", public_url="https://git.test", api_token="t", owner="msp",
    repo="rmm-scripts", branch="main", path="RMM", verify_ssl=True,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _serve(monkeypatch, handler):
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        gitea, "_client", lambda settings: httpx.AsyncClient(transport=httpx.MockTransport(record))
    )
    return requests


def test_open_patterns_cover_common_and_company_scripts_only():
    assert gitea_sign_in.open_file_patterns("") == ["common/**", "companies/**/?**"]
    assert gitea_sign_in.open_file_patterns("/Scripts/RMM/") == [
        "scripts/rmm/common/**", "scripts/rmm/companies/**/?**",
    ]
    assert gitea_sign_in.open_file_patterns("a*[b]") == [r"a\*\[b\]/common/**", r"a\*\[b\]/companies/**/?**"]


@pytest.mark.anyio
async def test_protect_branch_creates_the_rule(monkeypatch):
    requests = _serve(monkeypatch, lambda r: httpx.Response(404 if r.method == "GET" else 201, json={}))

    assert await gitea.protect_branch(SETTINGS, ["myportal", "Admin-1", ""], ["common/**"]) is True

    post = requests[-1]
    assert post.method == "POST"
    assert str(post.url) == "https://git.test/api/v1/repos/msp/rmm-scripts/branch_protections"
    body = json.loads(post.content)
    assert body["rule_name"] == "main"
    assert body["enable_push"] and body["enable_push_whitelist"] and body["enable_merge_whitelist"]
    assert body["push_whitelist_usernames"] == ["Admin-1", "myportal"]
    assert body["merge_whitelist_usernames"] == ["Admin-1", "myportal"]
    assert body["unprotected_file_patterns"] == "common/**"
    assert "protected_file_patterns" not in body


@pytest.mark.anyio
async def test_protect_branch_leaves_a_matching_rule_alone(monkeypatch):
    rule = {
        "enable_push": True, "enable_push_whitelist": True, "push_whitelist_usernames": ["MYPORTAL"],
        "push_whitelist_deploy_keys": False, "enable_merge_whitelist": True,
        "merge_whitelist_usernames": ["myportal"], "unprotected_file_patterns": "common/**",
        "required_approvals": 1,
    }
    requests = _serve(monkeypatch, lambda r: httpx.Response(200, json=rule))

    assert await gitea.protect_branch(SETTINGS, ["myportal"], ["common/**"]) is False
    assert [r.method for r in requests] == ["GET"]


@pytest.mark.anyio
async def test_protect_branch_updates_a_changed_rule(monkeypatch):
    rule = {"enable_push": True, "enable_push_whitelist": False, "unprotected_file_patterns": ""}
    requests = _serve(monkeypatch, lambda r: httpx.Response(200, json=rule))

    assert await gitea.protect_branch(SETTINGS, ["myportal"], ["common/**"]) is True
    assert requests[-1].method == "PATCH"
    assert str(requests[-1].url).endswith("/branch_protections/main")


@pytest.mark.anyio
async def test_protect_branch_reports_refusal(monkeypatch):
    _serve(monkeypatch, lambda r: httpx.Response(403))

    with pytest.raises(gitea.GiteaError):
        await gitea.protect_branch(SETTINGS, ["myportal"], [])


@pytest.mark.anyio
async def test_only_active_super_admins_and_the_token_account_may_change_links(monkeypatch):
    users = {
        1: {"id": 1, "is_super_admin": True, "is_active": True},
        2: {"id": 2, "is_super_admin": False, "is_active": True},
        3: {"id": 3, "is_super_admin": True, "is_active": False},
    }
    accounts = [
        {"user_id": 1, "gitea_login": "brad-1", "permission": "write"},
        {"user_id": 2, "gitea_login": "tech-2", "permission": "write"},
        {"user_id": 3, "gitea_login": "gone-3", "permission": "write"},
    ]
    calls = []

    async def load_settings():
        return SETTINGS

    async def token_login(settings):
        return "myportal"

    async def list_accounts():
        return accounts

    async def get_user(user_id):
        return users[user_id]

    async def protect(settings, writers, patterns):
        calls.append((settings.repo, settings.path, sorted(writers), patterns))
        if settings.repo == "Private-Files":
            raise gitea.GiteaError("refused")
        return True

    monkeypatch.setattr(gitea, "load_settings", load_settings)
    monkeypatch.setattr(gitea, "token_login", token_login)
    monkeypatch.setattr(gitea, "protect_branch", protect)
    monkeypatch.setattr(rmm_repo, "list_gitea_accounts", list_accounts)
    monkeypatch.setattr(user_repo, "get_user_by_id", get_user)
    monkeypatch.setattr(gitea_sign_in, "is_user_active", lambda user: user["is_active"])

    assert await gitea_sign_in.protect_company_links() == 1
    assert calls == [
        ("rmm-scripts", "RMM", ["brad-1", "myportal"], ["rmm/common/**", "rmm/companies/**/?**"]),
        ("Private-Files", "", ["brad-1", "myportal"], ["common/**", "companies/**/?**"]),
    ]


@pytest.mark.anyio
async def test_folder_maintenance_protects_company_links(monkeypatch):
    called = []

    async def noop():
        return 0

    async def protect():
        called.append(True)
        return 0

    monkeypatch.setattr(rmm_scripts, "ensure_folders", noop)
    monkeypatch.setattr(gitea_sign_in, "reconcile_accounts", noop)
    monkeypatch.setattr(gitea_sign_in, "protect_company_links", protect)

    await rmm_scripts._ensure_folders_once()

    assert called == [True]
