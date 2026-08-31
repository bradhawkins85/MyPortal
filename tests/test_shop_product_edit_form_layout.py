"""Regression checks for the task-oriented product editor layout."""

from pathlib import Path


TEMPLATE = Path("app/templates/admin/shop.html").read_text()
CSS = Path("app/static/css/app.css").read_text()
JAVASCRIPT = Path("app/static/js/shop_admin.js").read_text()


def test_editor_sections_follow_completion_order() -> None:
    headings = [
        ">Basic details<",
        ">Product content<",
        ">Pricing and inventory<",
        ">Subscription settings<",
        ">Product recommendations<",
        ">Features<",
    ]
    positions = [TEMPLATE.index(heading, TEMPLATE.index('id="product-edit-modal"')) for heading in headings]
    assert positions == sorted(positions)


def test_primary_fields_and_supporting_ui_are_present() -> None:
    assert 'class="form-field form-field--full"' in TEMPLATE
    assert 'class="product-content-grid"' in TEMPLATE
    assert '>Replace image<' in TEMPLATE
    assert '> Remove image<' in TEMPLATE
    assert '>Schedule price change<' in TEMPLATE
    assert 'placeholder="Search products by name or SKU"' in TEMPLATE
    assert 'Complementary products suggested at checkout.' in TEMPLATE
    assert 'Upgrade options suggested at checkout.' in TEMPLATE


def test_editor_is_responsive_sticky_and_protects_unsaved_work() -> None:
    assert "width: min(96vw, 1360px)" in CSS
    assert "position: sticky; bottom: 0" in CSS
    assert "@media (max-width: 760px)" in CSS
    assert "Discard your unsaved changes?" in JAVASCRIPT
    assert "submitButton.disabled = true" in JAVASCRIPT
