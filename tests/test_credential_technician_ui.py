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
