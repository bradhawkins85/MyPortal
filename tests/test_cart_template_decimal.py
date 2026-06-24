from decimal import Decimal
from pathlib import Path

from jinja2 import Environment


def test_cart_template_serializes_decimal_freight_total_for_javascript():
    template_text = Path("app/templates/shop/cart.html").read_text()
    assert "{{ (freight_total | float) | tojson }}" in template_text

    rendered = Environment(autoescape=True).from_string(
        "var freightTotal = {{ (freight_total | float) | tojson }};"
    ).render(freight_total=Decimal("12.34"))

    assert rendered == "var freightTotal = 12.34;"
