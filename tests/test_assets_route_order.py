"""Regression tests for assets feature route ordering."""

from fastapi.routing import APIRoute

from app.features.assets.routes import router


def test_assets_settings_route_precedes_asset_id_route():
    """Ensure /assets/settings is not captured by /assets/{asset_id}."""
    paths = [route.path for route in router.routes if isinstance(route, APIRoute)]

    assert paths.index("/assets/settings") < paths.index("/assets/{asset_id}")
