"""Tests for Xero invoice due-date term resolution."""

from app.services import xero


def test_company_invoice_due_days_override_environment(monkeypatch):
    monkeypatch.setenv("XERO_INVOICE_DUE_DAYS", "14")

    assert xero.resolve_invoice_due_days({"invoice_due_days": 30}) == 30


def test_invoice_due_days_fall_back_to_environment(monkeypatch):
    monkeypatch.setenv("XERO_INVOICE_DUE_DAYS", "21")

    assert xero.resolve_invoice_due_days({"invoice_due_days": None}) == 21


def test_invoice_due_days_default_to_fourteen_for_invalid_environment(monkeypatch):
    monkeypatch.setenv("XERO_INVOICE_DUE_DAYS", "not-a-number")

    assert xero.resolve_invoice_due_days({}) == 14


def test_zero_day_company_terms_are_supported(monkeypatch):
    monkeypatch.setenv("XERO_INVOICE_DUE_DAYS", "14")

    assert xero.resolve_invoice_due_days({"invoice_due_days": 0}) == 0
