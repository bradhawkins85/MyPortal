"""Regression checks for the M365 mail sync-history dialog."""

from pathlib import Path


TEMPLATE = (
    Path(__file__).resolve().parents[1] / "app/templates/admin/m365_mail.html"
).read_text(encoding="utf-8")


def test_sync_history_is_rendered_as_a_modal_after_the_account_list():
    history_modal = TEMPLATE.index('id="m365-mail-sync-history-modal"')
    account_table = TEMPLATE.index('id="m365-mail-accounts-table"')

    assert history_modal > account_table
    assert 'role="dialog"' in TEMPLATE[history_modal - 100 : history_modal + 200]
    assert 'aria-modal="true"' in TEMPLATE[history_modal - 100 : history_modal + 200]
    assert 'href="/admin/modules/m365-mail" aria-label="Close sync history"' in TEMPLATE


def test_sync_history_completed_time_is_formatted_in_browser_local_time():
    assert 'datetime="{{ run.completed_at.isoformat() }}"' in TEMPLATE
    assert "new Date(element.dateTime)" in TEMPLATE
    assert "completedAt.getFullYear()" in TEMPLATE
    assert "completedAt.getMonth() + 1" in TEMPLATE
    assert "completedAt.getHours()" in TEMPLATE
    assert "completedAt.getMinutes()" in TEMPLATE
