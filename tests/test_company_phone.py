"""Tests for storing a company's primary business phone number."""

from pathlib import Path

from app.schemas.companies import CompanyCreate, CompanyUpdate


TEMPLATE = Path("app/templates/admin/company_edit.html")


def test_company_phone_field_is_below_email_domains() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")

    email_domains = template.index('id="edit-company-email-domains"')
    company_phone = template.index('id="edit-company-phone"')

    assert company_phone > email_domains
    assert 'name="phone"' in template[company_phone:]
    assert 'type="tel"' in template[company_phone:]
    assert 'value="{{ form_data.phone }}"' in template[company_phone:]


def test_company_schemas_expose_phone() -> None:
    created = CompanyCreate(name="Acme", phone="+1 212 555 0100")
    updated = CompanyUpdate(phone="+44 20 7946 0958")

    assert created.phone == "+1 212 555 0100"
    assert updated.model_dump(exclude_unset=True) == {"phone": "+44 20 7946 0958"}
