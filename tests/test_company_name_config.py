import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_company_name_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("COMPANY_NAME", "  My Company  ")
    assert Settings(_env_file=None).company_name == "My Company"


@pytest.mark.parametrize("value", ["Company\nBcc: other@example.com", "Company\rName"])
def test_company_name_rejects_line_breaks(monkeypatch, value):
    monkeypatch.setenv("COMPANY_NAME", value)
    with pytest.raises(ValidationError, match="must not contain line breaks"):
        Settings(_env_file=None)
