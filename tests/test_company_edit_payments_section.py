"""Regression tests for the company Payments section."""

from pathlib import Path


TEMPLATE = Path("app/templates/admin/company_edit.html")


def _payments_section(template: str) -> str:
    start = template.index('data-company-edit-section="payments"')
    end = template.find('<section class="card card--panel ce-section"', start)
    return template[start:end if end != -1 else len(template)]


def test_payment_settings_are_grouped_in_payments_section():
    template = TEMPLATE.read_text()
    section = _payments_section(template)

    assert '<h2 class="card__title" id="ce-payments-title">Payments</h2>' in section
    for field_name in (
        "invoicePrepay",
        "invoicePostpay",
        "stripeEnabled",
        "isVip",
        "defaultTicketRepliesBillable",
        "requirePo",
        "invoiceDueDays",
    ):
        assert template.count(f'name="{field_name}"') == 1
        assert f'name="{field_name}"' in section


def test_moved_payment_settings_submit_with_company_settings_form():
    section = _payments_section(TEMPLATE.read_text())

    for field_name in (
        "invoicePrepay",
        "invoicePostpay",
        "stripeEnabled",
        "isVip",
        "defaultTicketRepliesBillable",
        "requirePo",
        "invoiceDueDays",
        "xeroAutoSendSubscriptionInvoices",
        "xeroAutoSendProductInvoices",
    ):
        start = section.index(f'name="{field_name}"')
        assert 'form="company-settings-form"' in section[start : section.index("/>", start)]
    # Saved from the page header; the in-section button is the no-JavaScript fallback.
    assert 'form="company-settings-form">Save company settings</button>' in section
    assert section.index("<noscript>") < section.index("Save company settings</button>")


def test_company_edit_loads_collapsible_section_state_script():
    template = TEMPLATE.read_text()

    assert "static/js/company_edit_sections.js" in template
