from pathlib import Path


def test_technician_credential_flow_uses_authenticated_deep_link_and_no_secret_url():
    template = Path("app/templates/admin/company_edit.html").read_text()

    assert "Create credential and grant" in template
    assert "Operations Manager" in template
    assert "No expiry" in template
    assert "new URL('/shared-credentials',location.origin)" in template
    assert "url.searchParams.set('credential',item.id)" in template
    assert "url.searchParams.set('secret'" not in template
    assert "url.searchParams.set('token'" not in template
    assert "last reveal" in template
    assert "data-revoke" in template


def test_technician_form_clears_secret_before_grant_request():
    template = Path("app/templates/admin/company_edit.html").read_text()
    clear_position = template.index("secret.value='';")
    grant_position = template.index("/standing-grants`,{method:'POST'")

    assert clear_position < grant_position


def test_credential_sections_follow_company_details_and_start_hidden():
    template = Path("app/templates/admin/company_edit.html").read_text()

    company_details = template.index('data-company-edit-section="general"')
    vault_rollout = template.index('data-company-edit-section="vault-rollout"')
    credentials = template.index('data-company-edit-section="credentials"')

    assert company_details < vault_rollout < credentials
    assert '<section class="card card--panel ce-section" id="vault-rollout"' in template
    assert '<section class="card card--panel ce-section" id="credentials"' in template
    # The shared credentials section stays hidden until the vault is enabled.
    assert " hidden " in template[credentials:template.index(">", credentials)]
    assert "data-ce-link=\"{{ key }}\"{% if key == 'credentials' %} hidden{% endif %}" in template
