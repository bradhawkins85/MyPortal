from decimal import Decimal

from app.services import shop_importer


def test_normalise_stock_date_slash_format():
    result = shop_importer._normalise_stock_date(  # type: ignore[attr-defined]
        "1/5/2024"
    )
    assert result == "2024-05-01"


def test_normalise_stock_date_iso_timezone():
    result = shop_importer._normalise_stock_date(  # type: ignore[attr-defined]
        "2024-05-01T10:30:00+10:00"
    )
    assert result == "2024-05-01"


def test_decimal_from_value_quantises_currency():
    result = shop_importer._decimal_from_value(  # type: ignore[attr-defined]
        "12.345"
    )
    assert result == Decimal("12.35")


def test_determine_extension_prefers_mime():
    result = shop_importer._determine_extension(  # type: ignore[attr-defined]
        "https://example.com/product", "image/png"
    )
    assert result == ".png"
