from unittest.mock import AsyncMock

import pytest

from app.features.staff import handlers
from app.schemas.staff_onboarding_workflows import WorkflowStepDefinition
from app.services import staff_onboarding_workflows as workflows


def test_share_step_is_in_both_catalogs_and_requires_secure_fields():
    for catalog in (handlers._ONBOARDING_STEP_CATALOG, handlers._OFFBOARDING_STEP_CATALOG):
        assert any(step["type"] == "share_myportal_credential" for step in catalog)
    with pytest.raises(ValueError, match="requires"):
        WorkflowStepDefinition(key="share", name="Share", config={"type": "share_myportal_credential"})
    with pytest.raises(ValueError, match="expir"):
        WorkflowStepDefinition(
            key="share", name="Share",
            config={"type": "share_myportal_credential", "credential_id": 1,
                    "selector_type": "external", "recipient_email": "a@example.test",
                    "reason": "handover", "permissions": ["reveal"],
                    "verification_code": "separate"},
        )


def test_nested_output_interpolates_and_share_url_is_redacted():
    values = {"credential_share": {"url": "https://portal.test/shared-credentials", "grant_id": 42}}
    assert workflows._resolve_template_value("${vars.credential_share.url}", vars_map=values).endswith("shared-credentials")
    assert workflows._resolve_template_value("Grant ${vars.credential_share.grant_id}", vars_map=values) == "Grant 42"
    assert workflows._redact_payload(values, secret_vars={"url"})["credential_share"]["url"] == "***redacted***"


@pytest.mark.anyio
async def test_share_execution_returns_configurable_output_without_plaintext(monkeypatch):
    create = AsyncMock(return_value={"grant_id": 9, "url": "/shared-credentials?credential=7", "kind": "standing"})
    monkeypatch.setattr("app.services.workflow_credential_shares.create_for_staff_workflow", create)
    result = await workflows._execute_policy_step(
        step={"type": "share_myportal_credential", "credential_id": 7,
              "selector_type": "staff", "staff_id": 3, "permissions": "reveal",
              "reason": "manager handover", "output_var": "credential_share"},
        company_id=2, staff={"id": 4, "requested_by_user_id": 5},
        policy_config={}, vars_map={}, execution_id=6, step_name="share-manager",
    )
    assert result["credential_share"]["grant_id"] == 9
    assert "password" not in repr(result).lower()
