from pathlib import Path


def test_linked_asset_chat_shows_success_toast_without_redirecting():
    script = Path("app/static/js/ticket_detail.js").read_text()

    assert "window.__portalToast.show('Chat created successfully.', { variant: 'success' });" in script
    assert "window.location.href = `/chat?room=${encodeURIComponent(data.room_id)}`;" not in script
