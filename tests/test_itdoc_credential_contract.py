"""Guardrails for the ITDOC 13/18 credential handling design."""

from pathlib import Path


CONTRACT = Path("docs/architecture/itdoc-credential-handling-contract.md")
SCOPE = Path("docs/architecture/itdoc-documentation-contract.md")


def _text(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_contract_defines_classes_and_independent_operations():
    text = _text(CONTRACT)
    for credential_class in (
        "`onboarding_password`",
        "`shared_account`",
        "`high_privilege_admin`",
        "`service_account`",
        "`recovery_item`",
    ):
        assert credential_class in text
    for permission in (
        "`create`",
        "`edit`",
        "`enumerate`",
        "`reveal`",
        "`share`",
        "`revoke`",
        "`rotate`",
        "`administer`",
    ):
        assert permission in text
    assert "Permissions are checked independently; none implies another" in text


def test_contract_covers_actor_matrix_and_grant_sources():
    text = _text(CONTRACT)
    for actor in (
        "Technician",
        "Manager",
        "Employee",
        "Company admin",
        "Super admin",
        "Service/workload identity",
        "Impersonated session",
    ):
        assert actor in text
    assert "direct item grant" in text
    assert "current job-title item grant" in text
    assert "manager can neither enumerate nor reveal unrelated employees" in text
    assert "named external user cannot enumerate or reveal" in text


def test_contract_denies_implicit_and_documentation_access():
    text = _text(CONTRACT)
    assert "There is no anonymous access" in text
    assert "blanket company visibility" in text
    assert "Merely being a company member, company admin, or a manager is never sufficient" in text
    assert "Ordinary documentation/RAG search" in text
    assert "customer publication" in text
    assert "email, tickets, KB/documentation bodies" in text


def test_contract_limits_onboarding_password_lifecycle():
    text = _text(CONTRACT)
    assert "change-at-first-use" in text
    assert "replace/invalidate the initial password after first use" in text
    assert "does not store an employee's privately chosen ongoing password" in text
    assert "not evidence of an employee's identity" in text


def test_scope_and_roadmap_include_separate_credential_workstream():
    text = _text(SCOPE)
    assert "ITDOC 01/18" in text
    assert "ITDOC 13/18" in text
    assert "ITDOC 00/18 roadmap (#4235)" in text
    assert "excluded from documentation search, publication, and export" in text
