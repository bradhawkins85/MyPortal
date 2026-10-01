from app.features.assets.routes import _CUSTOMER_ASSET_FIELDS, _customer_asset


def test_customer_asset_projection_is_allowlisted_and_excludes_internal_data():
    record = {
        "id": 12,
        "name": "Laptop",
        "status": "active",
        "customer_visible": True,
        "operational_notes": "internal note",
        "password": "must-not-leak",
        "syncro_asset_id": "internal-integration-id",
        "custom_fields": {"recovery_key": "secret"},
    }

    projected = _customer_asset(record)

    assert projected["id"] == 12
    assert projected["name"] == "Laptop"
    assert set(projected) == set(_CUSTOMER_ASSET_FIELDS)
    assert "customer_visible" not in projected
    assert "operational_notes" not in projected
    assert "password" not in projected
    assert "syncro_asset_id" not in projected
    assert "custom_fields" not in projected


def test_customer_asset_fields_do_not_include_secret_bearing_names():
    forbidden_fragments = {"credential", "password", "secret", "token", "note"}
    assert not any(
        fragment in field.casefold()
        for field in _CUSTOMER_ASSET_FIELDS
        for fragment in forbidden_fragments
    )
