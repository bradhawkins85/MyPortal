"""Browser-facing contracts for the dedicated IPAM and rack workspaces."""
from pathlib import Path

from fastapi.routing import APIRoute

from app.features.assets.routes import router


ROOT = Path(__file__).resolve().parents[1]


def _template(name: str) -> str:
    return (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")


def test_dedicated_pages_and_legacy_deep_link_are_registered():
    routes = {route.path: route for route in router.routes if isinstance(route, APIRoute)}
    assert routes["/ipam"].name == "ipam_page"
    assert routes["/racks"].name == "racks_page"
    assert routes["/infrastructure"].name == "infrastructure_page"


def test_ipam_page_has_only_ipam_controls_and_filterable_sortable_tables():
    page = _template("infrastructure/ipam.html")
    assert 'data-table-id="ip-networks"' in page
    assert 'data-table-id="ip-addresses"' in page
    assert 'data-table-filter="ip-networks"' in page
    assert 'data-table-filter="ip-addresses"' in page
    assert 'data-sort="string"' in page
    assert "/api/infrastructure/networks" in page
    assert "/api/infrastructure/addresses" in page
    assert "Add rack" not in page
    assert "Place existing asset" not in page


def test_rack_page_has_only_rack_controls_and_asset_links():
    page = _template("infrastructure/racks.html")
    assert "/api/infrastructure/racks" in page
    assert "/api/infrastructure/rack-equipment" in page
    assert 'href="/assets/{{ item.asset_id }}"' in page
    assert 'id="rack-{{ rack.id }}"' in page
    assert "Add network" not in page
    assert "Document address" not in page


def test_navigation_and_asset_cross_links_support_desktop_and_mobile_menu():
    navigation = _template("base.html")
    detail = _template("assets/detail.html")
    assert '<a href="/ipam"' in navigation
    assert '<span class="menu__label">IPAM</span>' in navigation
    assert '<a href="/racks"' in navigation
    assert '<span class="menu__label">Racks</span>' in navigation
    assert '/ipam#address-{{ address.id }}' in detail
    assert '/racks#rack-{{ placement.rack_id }}' in detail
