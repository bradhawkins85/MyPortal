from pathlib import Path


def test_attachments_are_collapsed_below_chat_on_user_ticket_detail():
    source = Path("app/templates/tickets/detail.html").read_text(encoding="utf-8")
    chat_position = source.index('<h2 class="card__title">Chat</h2>')
    attachments_position = source.index('<h2 class="card__title">Attachments</h2>')
    attachment_details_start = source.rfind("<details", 0, attachments_position)
    attachment_details_tag = source[attachment_details_start:source.index(">", attachment_details_start) + 1]

    assert chat_position < attachments_position
    assert 'class="card card--panel card-collapsible"' in attachment_details_tag
    assert " open" not in attachment_details_tag
