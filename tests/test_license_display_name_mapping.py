from app.repositories import licenses as license_repo


def test_normalise_license_sets_friendly_display_name_for_known_sku():
    row = {
        "platform": "O365_BUSINESS_ESSENTIALS",
        "name": "O365_BUSINESS_ESSENTIALS",
        "display_name": "O365_BUSINESS_ESSENTIALS",
        "company_id": 1,
        "count": 5,
        "allocated": 0,
    }

    result = license_repo._normalise_license(row)

    assert result["display_name"] == "Office 365 Business Essentials"


def test_normalise_license_preserves_display_name_for_unknown_sku():
    row = {
        "platform": "UNKNOWN_SKU",
        "name": "Custom",
        "display_name": "Custom",
        "company_id": 1,
        "count": 1,
        "allocated": 0,
    }

    result = license_repo._normalise_license(row)

    assert result["display_name"] == "Custom"
