from app.main import _staff_member_matches_company_email_domains


def test_staff_member_with_matching_domain_is_visible():
    member = {"email": "alice@example.com"}
    assert _staff_member_matches_company_email_domains(member, ["example.com"])


def test_staff_member_with_non_matching_domain_is_hidden():
    member = {"email": "alice@other.com"}
    assert not _staff_member_matches_company_email_domains(member, ["example.com"])


def test_staff_member_without_email_is_visible():
    member = {"email": ""}
    assert _staff_member_matches_company_email_domains(member, ["example.com"])


def test_staff_member_with_email_visible_when_company_has_no_domains():
    member = {"email": "alice@example.com"}
    assert _staff_member_matches_company_email_domains(member, [])

