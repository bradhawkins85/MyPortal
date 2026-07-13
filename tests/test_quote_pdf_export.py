from decimal import Decimal
import sys
import types

from starlette.requests import Request

# The application package imports the ticket attachment service during package
# initialisation, which imports python-magic. Stub it here so this focused
# quote PDF HTML test does not require libmagic native libraries.
sys.modules.setdefault(
    "magic",
    types.SimpleNamespace(
        from_buffer=lambda *args, **kwargs: "application/octet-stream"
    ),
)

from app.features.quotes.routes import _build_quote_pdf_html


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "scheme": "https",
            "server": ("portal.example", 443),
        }
    )


def test_quote_pdf_includes_product_image_but_removes_description_images():
    html = _build_quote_pdf_html(
        request=_request(),
        company={"name": "Example Co"},
        quote={"quote_number": "Q-1", "name": "Refresh"},
        items=[
            {
                "product_name": "Laptop",
                "sku": "LAP-1",
                "quantity": 1,
                "price": Decimal("1299.00"),
                "image_url": "/uploads/shop/laptop.png",
                "description": (
                    '<p>Fast laptop</p><img src="/uploads/details/internal.png" '
                    'alt="internal"><p>Done</p>'
                ),
            }
        ],
        include_line_images=True,
    )

    name_index = html.index("<h1>Laptop</h1>")
    image_index = html.index('class="detail-image"')
    details_index = html.index('class=\'description rich-text-viewer\'')

    assert name_index < image_index < details_index
    assert "https://portal.example/uploads/shop/laptop.png" in html
    assert "/uploads/details/internal.png" not in html
    assert "<img src" not in html
