from app.services.knowledge_base import _parse_ai_tag_text


def test_parse_ai_tag_text_filters_unhelpful_entries():
    raw = '["json", "Server Outage", "hawkinsit solutions", "normal"]'

    parsed = _parse_ai_tag_text(raw)

    assert parsed == ["server outage"]
