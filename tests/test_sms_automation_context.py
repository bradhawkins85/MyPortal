from pathlib import Path

from app.services import automations as automations_service
from app.services.tickets import _extract_sms_recipient_from_external_reference


def test_ticket_replied_trigger_event_is_available():
    values = {item["value"] for item in automations_service.list_trigger_events()}
    assert "tickets.replied" in values


def test_extract_sms_recipient_from_external_reference():
    assert _extract_sms_recipient_from_external_reference("sms:+61400111222:2026-06-16") == "+61400111222"
    assert _extract_sms_recipient_from_external_reference("sms:61400111222:2026-06-16") == "61400111222"
    assert _extract_sms_recipient_from_external_reference("syncro:123") is None


def test_sms_gateway_snippet_targets_sms_context():
    source = Path("app/static/js/automation.js").read_text()
    assert "'sms-gateway'" in source
    assert "{{ ticket.sms.recipient }}" in source
    assert "{{ ticket.latest_reply.body }}" in source
