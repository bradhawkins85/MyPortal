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


def test_quote_pdf_includes_product_image_but_removes_description_images(tmp_path, monkeypatch):
    image_path = tmp_path / "shop" / "laptop.png"
    image_path.parent.mkdir()
    image_path.write_bytes(b"png-bytes")

    class FakeMain:
        @staticmethod
        def _resolve_private_upload(file_path):
            return tmp_path / str(file_path)

    monkeypatch.setattr("app.features.quotes.routes._main", lambda: FakeMain)

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
    assert "data:image/png;base64,cG5nLWJ5dGVz" in html
    assert 'alt="Laptop"' in html
    assert "#e8f5e9" not in html
    assert "#22c55e" not in html
    assert "No image available" not in html
    assert "/uploads/details/internal.png" not in html
    assert "<img src" not in html


def test_quote_pdf_omits_image_card_when_store_image_is_unavailable():
    html = _build_quote_pdf_html(
        request=_request(),
        company={"name": "Example Co"},
        quote={"quote_number": "Q-2", "name": "Refresh"},
        items=[
            {
                "product_name": "Dock",
                "sku": "DOCK-1",
                "quantity": 1,
                "price": Decimal("199.00"),
                "image_url": None,
                "description": "<p>USB-C dock</p>",
            }
        ],
        include_line_images=True,
    )

    assert 'class="product-image-card"' not in html
    assert "No image available" not in html
    assert "#e8f5e9" not in html
    assert "#22c55e" not in html


def test_quote_pdf_omits_unreadable_private_upload_instead_of_showing_alt_text(monkeypatch):
    class FakeMain:
        @staticmethod
        def _resolve_private_upload(file_path):
            raise FileNotFoundError(str(file_path))

    monkeypatch.setattr("app.features.quotes.routes._main", lambda: FakeMain)

    html = _build_quote_pdf_html(
        request=_request(),
        company={"name": "Example Co"},
        quote={"quote_number": "Q-3", "name": "Refresh"},
        items=[
            {
                "product_name": "MeetingBar",
                "sku": "MEET-1",
                "quantity": 1,
                "price": Decimal("999.00"),
                "image_url": "/uploads/shop/missing.png",
                "description": "<p>Video bar</p>",
            }
        ],
        include_line_images=True,
    )

    assert 'class="product-image-card"' not in html
    assert 'alt="MeetingBar"' not in html
    assert "https://portal.example/uploads/shop/missing.png" not in html
