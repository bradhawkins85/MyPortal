from types import SimpleNamespace

import pytest

from app.repositories import users as user_repo
from app.services import sso_policy


class Fakes:
    def __init__(self) -> None:
        self.modes: dict[int, str] = {}
        self.tenant: dict[int, bool] = {}
        self.identities: dict[int, dict] = {}
        self.identity_counts: dict[int, int] = {}
        self.memberships: dict[int, set[int]] = {}
        self.domain_company: dict | None = None
        self.domains_seen: list[str] = []
        self.user_lookups = 0
        self.updates: list[tuple[int, dict]] = []
        self.audits: list[dict] = []
        self.companies: dict[int, dict] = {}
        self.credentials: dict[int, dict] = {}
        self.tenant_companies: list[int] = []
        self.tenant_map: dict[str, list[int]] | None = None
        self.tenants_seen: list[str] = []


@pytest.fixture
def fakes(monkeypatch):
    f = Fakes()

    async def get_company_by_id(company_id):
        if company_id in f.companies:
            return f.companies[company_id]
        if company_id in f.modes:
            return {"id": company_id, "sso_mode": f.modes[company_id]}
        return None

    async def get_company_by_email_domain(domain):
        f.domains_seen.append(domain)
        return f.domain_company

    async def update_company(company_id, **updates):
        f.updates.append((company_id, updates))
        f.modes[company_id] = updates["sso_mode"]
        return {}

    async def list_sso_company_ids_for_tenant(tenant_id):
        f.tenants_seen.append(tenant_id)
        if f.tenant_map is not None:
            return list(f.tenant_map.get(tenant_id, []))
        return f.tenant_companies

    async def get_credentials(company_id):
        return f.credentials.get(company_id)

    async def list_companies_for_user(user_id):
        return [{"company_id": cid} for cid in f.memberships.get(user_id, set())]

    async def get_for_user(user_id, provider="m365"):
        return f.identities.get(user_id)

    async def count_for_company(company_id, provider="m365"):
        return f.identity_counts.get(company_id, 0)

    async def get_user_by_email(email):
        f.user_lookups += 1
        return None

    async def record(**kwargs):
        f.audits.append(kwargs)

    async def company_has_tenant(company_id):
        return f.tenant.get(company_id, False)

    monkeypatch.setattr(sso_policy.company_repo, "get_company_by_id", get_company_by_id)
    monkeypatch.setattr(sso_policy.company_repo, "get_company_by_email_domain", get_company_by_email_domain)
    monkeypatch.setattr(sso_policy.company_repo, "update_company", update_company)
    monkeypatch.setattr(sso_policy.company_repo, "list_sso_company_ids_for_tenant", list_sso_company_ids_for_tenant)
    monkeypatch.setattr(sso_policy.m365_repo, "get_credentials", get_credentials)
    monkeypatch.setattr(sso_policy.user_company_repo, "list_companies_for_user", list_companies_for_user)
    monkeypatch.setattr(sso_policy.sso_identity_repo, "get_for_user", get_for_user)
    monkeypatch.setattr(sso_policy.sso_identity_repo, "count_for_company", count_for_company)
    monkeypatch.setattr(sso_policy.audit_service, "record", record)
    monkeypatch.setattr(user_repo, "get_user_by_email", get_user_by_email)
    monkeypatch.setattr(sso_policy, "company_has_tenant", company_has_tenant)
    return f


pytestmark = pytest.mark.asyncio


async def test_modes_constant():
    assert sso_policy.MODES == ("off", "optional", "required")


@pytest.mark.parametrize("stored", [None, "", "bogus", "REQUIRED "])
async def test_unknown_or_null_mode_reads_as_off(fakes, stored):
    fakes.companies[7] = {"id": 7, "sso_mode": stored}
    assert await sso_policy.get_company_sso_mode(7) == "off"


async def test_missing_company_reads_as_off(fakes):
    assert await sso_policy.get_company_sso_mode(404) == "off"


async def test_known_mode_is_returned(fakes):
    fakes.modes[7] = "optional"
    assert await sso_policy.get_company_sso_mode(7) == "optional"


async def test_company_tenant_id_prefers_csp_tenant(monkeypatch):
    async def get_company_by_id(company_id):
        return {"id": 7, "csp_tenant_id": "AAAA-Tenant"}

    async def get_credentials(company_id):
        return {"tenant_id": "bbbb-cred"}

    monkeypatch.setattr(sso_policy.company_repo, "get_company_by_id", get_company_by_id)
    monkeypatch.setattr(sso_policy.m365_repo, "get_credentials", get_credentials)
    assert await sso_policy.company_tenant_id(7) == "aaaa-tenant"
    assert await sso_policy.company_has_tenant(7) is True


async def test_company_tenant_id_falls_back_to_credentials(monkeypatch):
    async def get_company_by_id(company_id):
        return {"id": 7, "csp_tenant_id": "  "}

    async def get_credentials(company_id):
        return {"tenant_id": "BBBB-Cred"}

    monkeypatch.setattr(sso_policy.company_repo, "get_company_by_id", get_company_by_id)
    monkeypatch.setattr(sso_policy.m365_repo, "get_credentials", get_credentials)
    assert await sso_policy.company_tenant_id(7) == "bbbb-cred"
    assert await sso_policy.company_has_tenant(7) is True


async def test_company_tenant_id_none_without_either(monkeypatch):
    async def get_company_by_id(company_id):
        return {"id": 7, "csp_tenant_id": None}

    async def get_credentials(company_id):
        return {"tenant_id": ""}

    monkeypatch.setattr(sso_policy.company_repo, "get_company_by_id", get_company_by_id)
    monkeypatch.setattr(sso_policy.m365_repo, "get_credentials", get_credentials)
    assert await sso_policy.company_tenant_id(7) is None
    assert await sso_policy.company_has_tenant(7) is False


async def test_company_tenant_id_none_when_nothing_exists(monkeypatch):
    async def none(company_id):
        return None

    monkeypatch.setattr(sso_policy.company_repo, "get_company_by_id", none)
    monkeypatch.setattr(sso_policy.m365_repo, "get_credentials", none)
    assert await sso_policy.company_tenant_id(7) is None


async def test_companies_for_tenant_wraps_repo(fakes):
    fakes.tenant_companies = [3, 4]
    assert await sso_policy.companies_for_tenant("T") == [3, 4]


async def test_user_company_ids_combines_primary_and_memberships(fakes):
    fakes.memberships[1] = {8, 9}
    assert await sso_policy.user_company_ids({"id": 1, "company_id": 7}) == {7, 8, 9}
    assert await sso_policy.user_company_ids({"id": 2, "company_id": None}) == set()


@pytest.mark.parametrize("raw", ["", "no-at-sign", "a@", "@b.com", "  "])
async def test_discover_malformed_is_off(fakes, raw):
    assert await sso_policy.discover(raw) == "off"


async def test_discover_uses_domain_mode_and_requires_tenant(fakes):
    fakes.domain_company = {"id": 7, "sso_mode": "required"}
    fakes.tenant[7] = True
    assert await sso_policy.discover(" Alice@Contoso.COM ") == "required"
    assert fakes.domains_seen[-1] == "contoso.com"
    fakes.tenant[7] = False
    assert await sso_policy.discover("alice@contoso.com") == "off"


async def test_discover_unknown_domain_is_off(fakes):
    assert await sso_policy.discover("alice@nowhere.com") == "off"


async def test_discover_unknown_mode_value_is_off(fakes):
    fakes.domain_company = {"id": 7, "sso_mode": "bogus"}
    fakes.tenant[7] = True
    assert await sso_policy.discover("alice@contoso.com") == "off"


async def test_discover_never_looks_up_users(fakes):
    await sso_policy.discover("alice@contoso.com")
    assert fakes.user_lookups == 0


async def test_password_login_refused_only_for_linked_required_non_super(fakes):
    user = {"id": 1, "company_id": 7, "is_super_admin": 0}
    fakes.modes[7] = "required"
    fakes.tenant_map = {"tenant-a": [7]}
    fakes.identities[1] = {"id": 9, "tenant_id": "tenant-a"}
    assert await sso_policy.password_login_allowed(user) is False
    assert await sso_policy.password_login_allowed({**user, "is_super_admin": 1}) is True
    fakes.identities.pop(1)
    assert await sso_policy.password_login_allowed(user) is True
    fakes.identities[1] = {"id": 9, "tenant_id": "tenant-a"}
    fakes.modes[7] = "optional"
    assert await sso_policy.password_login_allowed(user) is True


async def test_password_login_refused_via_membership_company(fakes):
    user = {"id": 1, "company_id": 7, "is_super_admin": 0}
    fakes.modes[7] = "optional"
    fakes.modes[8] = "required"
    fakes.memberships[1] = {8}
    fakes.tenant_map = {"tenant-a": [8]}
    fakes.identities[1] = {"id": 9, "tenant_id": "tenant-a"}
    assert await sso_policy.password_login_allowed(user) is False


async def test_password_allowed_when_identity_tenant_maps_to_no_sso_company(fakes):
    user = {"id": 1, "company_id": 7, "is_super_admin": 0}
    fakes.modes[7] = "required"
    fakes.identities[1] = {"id": 9, "tenant_id": "tenant-a"}
    fakes.tenant_map = {}
    assert await sso_policy.password_login_allowed(user) is True
    assert fakes.tenants_seen == ["tenant-a"]


async def test_password_allowed_when_identity_tenant_companies_exclude_the_user(fakes):
    user = {"id": 1, "company_id": 7, "is_super_admin": 0}
    fakes.modes[7] = "required"
    fakes.identities[1] = {"id": 9, "tenant_id": "tenant-a"}
    fakes.tenant_map = {"tenant-a": [12], "tenant-b": [7]}
    assert await sso_policy.password_login_allowed(user) is True


async def test_password_refused_when_identity_tenant_still_covers_a_user_company(fakes):
    user = {"id": 1, "company_id": 7, "is_super_admin": 0}
    fakes.modes[7] = "required"
    fakes.memberships[1] = {12}
    fakes.identities[1] = {"id": 9, "tenant_id": "tenant-a"}
    fakes.tenant_map = {"tenant-a": [12]}
    assert await sso_policy.password_login_allowed(user) is False


async def test_password_allowed_when_identity_has_no_tenant(fakes):
    user = {"id": 1, "company_id": 7, "is_super_admin": 0}
    fakes.modes[7] = "required"
    fakes.identities[1] = {"id": 9, "tenant_id": None}
    fakes.tenant_map = {"": [7]}
    assert await sso_policy.password_login_allowed(user) is True


async def test_can_manage_link_rules(fakes):
    admin = {"id": 2, "is_super_admin": 0}
    target = {"id": 1, "company_id": 7, "is_super_admin": 0}
    assert await sso_policy.can_manage_link(admin, target, 7, {7}) is True
    assert await sso_policy.can_manage_link(admin, {**target, "is_super_admin": 1}, 7, {7}) is False
    fakes.memberships[1] = {7, 8}
    assert await sso_policy.can_manage_link(admin, target, 7, {7}) is False
    assert await sso_policy.can_manage_link({"id": 3, "is_super_admin": 1}, target, 7, set()) is True
    assert await sso_policy.can_manage_link(admin, target, 9, {7}) is False


async def test_set_required_needs_tenant_and_a_linked_user(fakes):
    actor = {"id": 2}
    request = SimpleNamespace()
    fakes.modes[7] = "optional"

    with pytest.raises(ValueError, match="Connect this company's Microsoft 365 tenant"):
        await sso_policy.set_company_sso_mode(7, "required", actor=actor, request=request)

    fakes.tenant[7] = True
    with pytest.raises(ValueError, match="Link at least one user"):
        await sso_policy.set_company_sso_mode(7, "required", actor=actor, request=request)
    assert fakes.updates == [] and fakes.audits == []

    fakes.identity_counts[7] = 1
    await sso_policy.set_company_sso_mode(7, "required", actor=actor, request=request)
    assert fakes.updates == [(7, {"sso_mode": "required"})]
    assert fakes.audits == [{
        "action": "company.sso_mode.update",
        "request": request,
        "user_id": 2,
        "entity_type": "company",
        "entity_id": 7,
        "before": {"sso_mode": "optional"},
        "after": {"sso_mode": "required"},
    }]


async def test_set_optional_needs_no_tenant(fakes):
    fakes.modes[7] = "off"
    await sso_policy.set_company_sso_mode(7, "optional", actor={"id": 2}, request=None)
    assert fakes.updates == [(7, {"sso_mode": "optional"})]
    assert fakes.audits[0]["before"] == {"sso_mode": "off"}


async def test_set_unknown_mode_rejected(fakes):
    with pytest.raises(ValueError, match="Unknown sign-in mode."):
        await sso_policy.set_company_sso_mode(7, "bogus", actor={"id": 2}, request=None)
    assert fakes.updates == []
