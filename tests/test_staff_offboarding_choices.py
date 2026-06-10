from app.features.staff.helpers import (
    _filter_staff_for_offboarding_choices,
    _staff_member_is_offboarding_mail_choice,
)


def test_offboarding_mail_choice_requires_company_domain():
    assert _staff_member_is_offboarding_mail_choice(
        {"email": "alice@example.com"}, ["example.com"]
    )
    assert not _staff_member_is_offboarding_mail_choice(
        {"email": "external@other.com"}, ["example.com"]
    )


def test_offboarding_mail_choice_requires_email_address():
    assert not _staff_member_is_offboarding_mail_choice({"email": ""}, ["example.com"])
    assert not _staff_member_is_offboarding_mail_choice({"email": None}, ["example.com"])


def test_filter_staff_for_offboarding_choices_removes_duplicates_and_external_users():
    staff_members = [
        {"id": 1, "email": "alice@example.com"},
        {"id": 2, "email": "ALICE@example.com"},
        {"id": 3, "email": "bob@example.com"},
        {"id": 4, "email": "external@other.com"},
        {"id": 5, "email": ""},
    ]

    choices = _filter_staff_for_offboarding_choices(staff_members, ["example.com"])

    assert choices == [
        {"id": 1, "email": "alice@example.com"},
        {"id": 3, "email": "bob@example.com"},
    ]
