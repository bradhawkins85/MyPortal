from app.services import tickets


def test_finalise_tags_filters_unhelpful_slugs():
    raw_tags = ["json", "printer-error", "tags", "normal"]

    filtered = tickets._finalise_tags(raw_tags, {})

    assert filtered == ["printer-error"]


def test_finalise_tags_does_not_pad_with_generic_or_workflow_tags():
    ticket = {
        "subject": "Please help with Outlook crash",
        "description": "Hello, outlook crashes whenever attachments open. Thanks",
        "status": "open",
        "priority": "high",
        "category": "Email",
    }

    filtered = tickets._finalise_tags(["outlook-crash"], ticket)

    assert filtered == ["outlook-crash"]


def test_finalise_tags_falls_back_to_topic_keywords_when_ai_returns_nothing():
    ticket = {
        "subject": "Please help with Outlook crash",
        "status": "open",
        "priority": "high",
        "category": "Email",
    }

    filtered = tickets._finalise_tags([], ticket)

    assert filtered == ["email", "outlook", "crash"]
