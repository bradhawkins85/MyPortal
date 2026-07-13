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


def test_filters_match_ticket_body_contains_spam_phrase():
    context = {
        "ticket": {
            "subject": "Hello",
            "body": "Limited offer: buy followers and boost your rankings today.",
        }
    }
    filters = {"contains": {"ticket.body": "buy followers"}}

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


def test_filters_match_ai_tags_sequence_contains_expected_tag():
    context = {"ticket": {"ai_tags": ["printer", "network-outage"]}}
    filters = {"match": {"ticket.ai_tags": "network-%"}}

    assert automations_service._filters_match(filters, context)


def test_filters_match_ticket_boolean_and_time_automation_fields():
    context = {
        "ticket": {
            "has_attachments": True,
            "has_open_tasks": False,
            "billable_minutes": 45,
            "non_billable_minutes": 10,
        }
    }

    assert automations_service._filters_match(
        {
            "all": [
                {"match": {"ticket.has_attachments": True}},
                {"match": {"ticket.has_open_tasks": False}},
                {"greater_than": {"ticket.billable_minutes": 30}},
                {"less_than_or_equal": {"ticket.non_billable_minutes": 10}},
            ]
        },
        context,
    )


def test_filters_match_string_operator_variants():
    context = {"ticket": {"subject": "Network outage for payroll"}}

    assert automations_service._filters_match(
        {"starts_with": {"ticket.subject": "Network"}}, context
    )
    assert automations_service._filters_match(
        {"ends_with": {"ticket.subject": "payroll"}}, context
    )
    assert automations_service._filters_match(
        {"contains": {"ticket.subject": "outage"}}, context
    )
    assert automations_service._filters_match(
        {"not_contains": {"ticket.subject": "printer"}}, context
    )
    assert automations_service._filters_match(
        {"regex": {"ticket.subject": r"Network\s+outage"}}, context
    )


def test_filters_match_string_operators_support_sequences():
    context = {"ticket": {"ai_tags": ["printer", "network-outage"]}}

    assert automations_service._filters_match(
        {"contains": {"ticket.ai_tags": "network"}}, context
    )
    assert automations_service._filters_match(
        {"not_contains": {"ticket.ai_tags": "billing"}}, context
    )
    assert not automations_service._filters_match(
        {"not_contains": {"ticket.ai_tags": "printer"}}, context
    )


def test_filters_match_custom_regex_rejects_invalid_or_oversized_patterns():
    context = {"ticket": {"subject": "Network outage for payroll"}}

    assert not automations_service._filters_match(
        {"regex": {"ticket.subject": "["}}, context
    )
    assert not automations_service._filters_match(
        {"regex": {"ticket.subject": "a" * 257}}, context
    )
