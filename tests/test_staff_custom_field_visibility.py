from app.repositories import staff_custom_fields


def test_staff_custom_field_visible_without_restrictions():
    assert staff_custom_fields._definition_is_visible_to_requester({}) is True


def test_staff_custom_field_visible_to_matching_job_title():
    definition = {"visible_to_job_titles": "Manager, Department Manager"}

    assert (
        staff_custom_fields._definition_is_visible_to_requester(
            definition, requester_job_title="department manager"
        )
        is True
    )


def test_staff_custom_field_visible_to_matching_requester_email():
    definition = {
        "visible_to_requester_emails": "approver@example.com, manager@example.com"
    }

    assert (
        staff_custom_fields._definition_is_visible_to_requester(
            definition, requester_email="Manager@Example.com"
        )
        is True
    )


def test_staff_custom_field_hidden_when_no_visibility_match():
    definition = {
        "visible_to_job_titles": "Manager",
        "visible_to_requester_emails": "approver@example.com",
    }

    assert (
        staff_custom_fields._definition_is_visible_to_requester(
            definition,
            requester_email="staff@example.com",
            requester_job_title="Technician",
        )
        is False
    )
