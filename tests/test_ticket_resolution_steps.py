from pathlib import Path

from app.services import rag_index
from app.services import tickets as tickets_service


def test_resolution_prompt_emphasises_flagged_entries_without_dropping_context():
    prompt = tickets_service._render_resolution_prompt(
        {"id": 7, "subject": "Printer offline", "description": "Cannot print", "status": "resolved"},
        [
            {"id": 1, "body": "Reported error 42", "is_resolution_step": False},
            {"id": 2, "body": "Replaced the damaged cable", "is_resolution_step": True},
        ],
    )

    assert "FLAGGED RESOLUTION STEP" in prompt
    assert "Replaced the damaged cable" in prompt
    assert "Reported error 42" in prompt


def test_resolution_response_is_sanitised():
    result = tickets_service._extract_resolution_steps(
        '{"resolution_steps":"<ul><li>Restarted service</li></ul><script>alert(1)</script>"}'
    )

    assert result is not None
    assert "Restarted service" in result
    assert "script" not in result


def test_ticket_rag_document_contains_resolution_steps():
    document = rag_index.document_from_source(
        "tickets",
        {
            "id": 9,
            "company_id": 3,
            "subject": "VPN failure",
            "resolution_steps": "<ol><li>Renewed the certificate</li></ol>",
            "permission_scope": {"version": 1, "visibility": "super_admin"},
        },
    )

    assert document is not None
    assert "[Resolution steps]" in document.text
    assert "Renewed the certificate" in document.text


def test_admin_ticket_template_exposes_resolution_controls():
    template = Path("app/templates/admin/ticket_detail.html").read_text(encoding="utf-8")

    assert 'name="isResolutionStep"' in template
    assert 'data-ticket-resolution-panel' in template
    assert 'name="resolutionSteps"' in template
    assert 'data-resolution-reprocess' in template
    assert 'replies/{{ reply.id }}/resolution-step' in template
    assert "Resolution step" in template
