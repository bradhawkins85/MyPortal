from pathlib import Path


def test_change_type_is_immediately_before_review_date() -> None:
    template = Path("app/templates/admin/ticket_detail.html").read_text(encoding="utf-8")

    change_type_index = template.index('<span class="form-label">Change Type</span>')
    review_date_index = template.index('for="ticket-review-date-detail">Review Date</label>')
    intervening_markup = template[change_type_index:review_date_index]

    assert change_type_index < review_date_index
    assert intervening_markup.count('<div class="form-field">') == 1
