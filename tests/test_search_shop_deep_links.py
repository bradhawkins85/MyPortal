from pathlib import Path


def test_search_results_deep_link_to_shop_modals() -> None:
    agent_script = Path("app/static/js/agent.js").read_text(encoding="utf-8")
    shop_script = Path("app/static/js/shop.js").read_text(encoding="utf-8")
    packages_script = Path("app/static/js/shop_packages.js").read_text(encoding="utf-8")
    packages_template = Path("app/templates/shop/packages.html").read_text(encoding="utf-8")

    assert "`/shop?product=${id}`" in agent_script
    assert "`/shop/packages?package=${id}`" in agent_script
    assert "get('product')" in shop_script
    assert "openProductDetails(requestedProductId)" in shop_script
    assert 'id="package-details-modal"' in packages_template
    assert "get('package')" in packages_script
    assert "renderPackageDetails(pkg)" in packages_script
