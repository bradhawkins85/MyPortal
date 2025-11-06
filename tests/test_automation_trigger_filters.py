from app.services import automations as automations_service


def test_filters_match_subject_like_prefix():
    context = {"ticket": {"subject": "My computer will not boot"}}
    filters = {"match": {"ticket.subject": "My computer%"}}

    assert automations_service._filters_match(filters, context)


def test_filters_match_subject_like_suffix():
    context = {"ticket": {"subject": "Server wont turn on"}}
    filters = {"match": {"ticket.subject": "% wont turn on"}}

    assert automations_service._filters_match(filters, context)


def test_filters_match_subject_like_infix():
    context = {"ticket": {"subject": "New User Azure Onboarding"}}
    filters = {"match": {"ticket.subject": "New User % Onboarding"}}

    assert automations_service._filters_match(filters, context)


def test_filters_like_handles_escape_sequences():
    context = {"ticket": {"subject": "CPU at 100%"}}
    filters = {"match": {"ticket.subject": r"CPU at 100\%"}}

    assert automations_service._filters_match(filters, context)


def test_filters_like_list_options_supported():
    context = {"ticket": {"subject": "My computer caught fire"}}
    filters = {"match": {"ticket.subject": ["Escalate immediately", "My computer%"]}}

    assert automations_service._filters_match(filters, context)


def test_filters_like_requires_string_actual_value():
    context = {"ticket": {"subject": 404}}
    filters = {"match": {"ticket.subject": "% wont turn on"}}

    assert not automations_service._filters_match(filters, context)
