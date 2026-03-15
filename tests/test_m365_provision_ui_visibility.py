"""Tests verifying that M365 provision UI is visible without admin credentials.

The provision routes use PKCE and work without admin credentials being configured.
The UI should show the provision section to super admins regardless of whether
admin credentials are configured.
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient as HttpxAsyncClient

from app.main import app

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "app", "templates")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def async_client():
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def _load_template(relative_path: str) -> str:
    with open(os.path.join(_TEMPLATE_DIR, relative_path)) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Template source checks
# ---------------------------------------------------------------------------

def test_m365_index_provision_section_not_gated_by_admin_credentials():
    """m365/index.html shows the provision section to super admins without admin credentials."""
    source = _load_template("m365/index.html")
    assert "admin_credentials_configured and is_super_admin" not in source, (
        "m365/index.html must not gate the provision section on admin_credentials_configured"
    )


def test_company_edit_provision_section_not_gated_by_admin_credentials():
    """admin/company_edit.html shows the provision section without m365_admin_credentials_configured gate."""
    source = _load_template("admin/company_edit.html")
    assert "{% if m365_admin_credentials_configured %}" not in source, (
        "admin/company_edit.html must not gate the provision section on m365_admin_credentials_configured"
    )


def test_csp_customers_provision_button_not_gated_by_admin_credentials():
    """admin/csp_customers.html shows the Provision M365 button without admin_credentials_configured gate."""
    source = _load_template("admin/csp_customers.html")

    provision_idx = source.find(">Provision M365</a>")
    assert provision_idx != -1, "Provision M365 button should exist in template"

    # The text between the last {% if admin_credentials_configured %} and the
    # provision button (if any) should not have an un-closed if for that variable.
    preceding = source[:provision_idx]
    # Count opens/closes for admin_credentials_configured blocks
    opens = preceding.count("{% if admin_credentials_configured %}")
    closes = preceding.count("{% endif %}")
    # The number of endifs should have closed all the opens before the button
    # so there should be no active admin_credentials_configured block around it.
    # More precisely: every {% if admin_credentials_configured %} before the
    # button must have a matching {% endif %} also before the button.
    # A simple proxy: the last unclosed block should not be admin_credentials.
    last_if_idx = preceding.rfind("{% if admin_credentials_configured %}")
    if last_if_idx != -1:
        # Find the matching endif after this if (within the preceding text)
        endif_idx = preceding.find("{% endif %}", last_if_idx)
        assert endif_idx != -1, (
            "The Provision M365 button must not be inside an "
            "{% if admin_credentials_configured %} block"
        )


def test_companies_csp_link_not_gated_by_admin_credentials():
    """admin/companies.html shows the CSP/Lighthouse link to super admins without admin credentials."""
    source = _load_template("admin/companies.html")

    csp_idx = source.find("CSP / Lighthouse")
    assert csp_idx != -1, "CSP / Lighthouse link should exist in template"

    preceding = source[:csp_idx]
    last_if_idx = preceding.rfind("{% if")
    last_if_block = preceding[last_if_idx:]
    assert "admin_credentials_configured" not in last_if_block, (
        "The CSP / Lighthouse link must not be gated by admin_credentials_configured"
    )
    assert "is_super_admin" in last_if_block, (
        "The CSP / Lighthouse link should be shown to super admins"
    )


# ---------------------------------------------------------------------------
# Route behaviour: provision auth URL uses /organizations endpoint
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_m365_provision_uses_organizations_endpoint_without_admin_credentials(
    async_client: HttpxAsyncClient,
):
    """GET /m365/provision redirects to /organizations even without admin credentials."""
    from urllib.parse import urlparse, parse_qs

    with (
        patch("app.main._load_license_context", new_callable=AsyncMock) as mock_ctx,
        patch(
            "app.main._get_m365_admin_credentials",
            new_callable=AsyncMock,
            return_value=(None, None),
        ),
    ):
        mock_ctx.return_value = (
            {"id": 1, "is_super_admin": True},
            {},
            None,
            42,
            None,
        )
        response = await async_client.get(
            "/m365/provision?tenant_id=customer-tenant-id",
            follow_redirects=False,
        )

    assert response.status_code == 303
    parsed = urlparse(response.headers["location"])
    qs = parse_qs(parsed.query)

    assert "organizations" in parsed.path, (
        "Provision route must use /organizations to avoid AADSTS700016"
    )
    assert qs.get("domain_hint", [None])[0] == "customer-tenant-id", (
        "Provision route must pass domain_hint to guide admin to correct tenant"
    )
