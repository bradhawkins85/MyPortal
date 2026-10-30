import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.repositories import companies as company_repo
from app.repositories import user_companies as user_company_repo
from app.services import company_management

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def directory(monkeypatch):
    companies = {
        1: {"id": 1, "name": "beta", "archived": 0},
        2: {"id": 2, "name": "Alpha", "archived": 0},
        3: {"id": 3, "name": "Gamma", "archived": 1},
    }
    memberships: list[dict] = []

    async def list_companies(include_archived=False):
        return [
            dict(company)
            for company in companies.values()
            if include_archived or not company["archived"]
        ]

    async def get_company_by_id(company_id):
        return companies.get(company_id)

    async def list_companies_for_user(user_id):
        return list(memberships)

    monkeypatch.setattr(company_repo, "list_companies", list_companies)
    monkeypatch.setattr(company_repo, "get_company_by_id", get_company_by_id)
    monkeypatch.setattr(user_company_repo, "list_companies_for_user", list_companies_for_user)
    return memberships


@pytest.mark.anyio("asyncio")
async def test_super_admin_gets_all_non_archived_companies_sorted(directory):
    is_super_admin, companies, lookup = await company_management.management_scope(
        {"id": 1, "is_super_admin": True}
    )

    assert is_super_admin is True
    assert [company["id"] for company in companies] == [2, 1]
    assert lookup == {}


@pytest.mark.anyio("asyncio")
async def test_super_admin_includes_archived_when_requested(directory):
    _, companies, _ = await company_management.management_scope(
        {"id": 1, "is_super_admin": True}, include_archived=True
    )

    assert [company["id"] for company in companies] == [2, 1, 3]


@pytest.mark.anyio("asyncio")
async def test_company_admin_membership_gets_that_company(directory):
    membership = {"company_id": 2, "is_admin": True}
    directory.append(membership)

    is_super_admin, companies, lookup = await company_management.management_scope(
        {"id": 5, "is_super_admin": False}
    )

    assert is_super_admin is False
    assert [company["id"] for company in companies] == [2]
    assert lookup == {2: membership}


@pytest.mark.anyio("asyncio")
async def test_member_without_company_admin_permission_is_denied(directory):
    directory.append({"company_id": 2, "can_manage_assets": True})

    with pytest.raises(HTTPException) as exc_info:
        await company_management.management_scope({"id": 5, "is_super_admin": False})

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Access denied"


@pytest.mark.anyio("asyncio")
async def test_explicit_no_access_overrides_is_admin(directory):
    directory.append(
        {"company_id": 2, "is_admin": True, "menu_permissions": {"menu.admin.company": "none"}}
    )

    with pytest.raises(HTTPException) as exc_info:
        await company_management.management_scope({"id": 5, "is_super_admin": False})

    assert exc_info.value.status_code == 403


@pytest.mark.anyio("asyncio")
async def test_member_without_memberships_is_denied(directory):
    with pytest.raises(HTTPException) as exc_info:
        await company_management.management_scope({"id": 5, "is_super_admin": False})

    assert exc_info.value.status_code == 403


@pytest.mark.anyio("asyncio")
async def test_archived_company_excluded_unless_requested(directory):
    directory.append({"company_id": 2, "is_admin": True})
    directory.append({"company_id": 3, "is_admin": True})
    user = {"id": 5, "is_super_admin": False}

    _, companies, lookup = await company_management.management_scope(user)
    assert [company["id"] for company in companies] == [2]
    assert set(lookup) == {2, 3}

    _, companies, _ = await company_management.management_scope(user, include_archived=True)
    assert [company["id"] for company in companies] == [2, 3]


@pytest.mark.anyio("asyncio")
async def test_only_archived_company_is_denied(directory):
    directory.append({"company_id": 3, "is_admin": True})

    with pytest.raises(HTTPException) as exc_info:
        await company_management.management_scope({"id": 5, "is_super_admin": False})

    assert exc_info.value.status_code == 403


@pytest.mark.anyio("asyncio")
async def test_non_numeric_company_id_is_skipped(directory):
    directory.append({"company_id": "abc", "is_admin": True})
    directory.append({"company_id": "1", "is_admin": True})

    _, companies, lookup = await company_management.management_scope(
        {"id": 5, "is_super_admin": False}
    )

    assert [company["id"] for company in companies] == [1]
    assert set(lookup) == {1}


@pytest.mark.anyio("asyncio")
async def test_menu_can_override_is_used(directory):
    directory.append({"company_id": 1})
    directory.append({"company_id": 2, "is_admin": True})
    calls: list[tuple[int, str]] = []

    def menu_can(user, membership, key, *, write=False):
        calls.append((membership["company_id"], key))
        return membership["company_id"] == 1

    _, companies, lookup = await company_management.management_scope(
        {"id": 5, "is_super_admin": False}, menu_can=menu_can
    )

    assert [company["id"] for company in companies] == [1]
    assert set(lookup) == {1}
    assert calls == [(1, "menu.admin.company"), (2, "menu.admin.company")]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            modules.add(base)
            modules.update(base + "." + alias.name for alias in node.names)
    return modules


def _is_forbidden(module: str) -> bool:
    return module in {"app.main", "app.features.companies"} or module.startswith(
        ("app.main.", "app.features.companies.")
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "app/api/routes/company_sso.py",
        "app/services/company_management.py",
        "app/security/menu_permissions.py",
        "app/services/sso_admin.py",
        "app/services/sso_policy.py",
    ],
)
def test_module_does_not_import_main_or_companies_pack(relative_path):
    modules = _imported_modules(REPO_ROOT / relative_path)

    assert not [module for module in modules if _is_forbidden(module)]
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert "from app import main" not in source
