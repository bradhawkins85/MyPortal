import pytest

from app.services.cron_expression import for_croniter, parse, validate


@pytest.mark.parametrize(
    "expression,year",
    [
        ("0 9 1 1 *", "*"),
        ("0 9 1 1 * *", "*"),
        ("0 9 1 1 * 2027", "2027"),
        ("0 9 1 1 * 2027-2029", "2027-2029"),
        ("0 9 1 1 * 2027,2029", "2027,2029"),
        ("0 9 1 1 * 2027,2029-2031", "2027,2029-2031"),
        ("0 9 L * * 2028", "2028"),
    ],
)
def test_supported_year_syntax(expression, year):
    assert parse(expression)[5] == year


@pytest.mark.parametrize(
    "expression,message",
    [
        ("0 9 1 1", "5 or 6 fields"),
        ("0 9 1 1 * * extra", "5 or 6 fields"),
        ("0 9 1 1 * 1969", "between 1970 and 2099"),
        ("0 9 1 1 * 2100", "between 1970 and 2099"),
        ("0 9 1 1 * 2029-2027", "ascending order"),
        ("0 9 1 1 * 2027,,2029", "comma-separated list"),
        ("0 9 1 1 * 27", "four digits"),
        ("0 9 1 1 * 2027/2", "step expressions"),
    ],
)
def test_invalid_years_have_clear_errors(expression, message):
    with pytest.raises(ValueError, match=message):
        validate(expression)


def test_croniter_conversion_inserts_zero_seconds_before_year():
    assert for_croniter("0 9 1 1 * 2027") == "0 9 1 1 * 0 2027"
