from app.services import product_descriptions


def test_extract_features_from_key_value_lines():
    description = """
    CPU: Intel Core i7
    Memory - 16GB DDR5
    Storage | 512GB SSD
    Marketing paragraph without a separator.
    """

    features = product_descriptions.extract_features(description)

    assert features == [
        {"name": "CPU", "value": "Intel Core i7", "position": 0},
        {"name": "Memory", "value": "16GB DDR5", "position": 1},
        {"name": "Storage", "value": "512GB SSD", "position": 2},
    ]


def test_parse_ai_payload_sanitizes_html_and_features():
    html, features = product_descriptions._parse_ai_payload(
        {
            "description_html": "<h3>Specs</h3><script>alert(1)</script><p>Safe</p>",
            "features": [
                {"name": "Warranty", "value": "3 years"},
                {"name": "", "value": "ignored"},
            ],
        }
    )

    assert "script" not in (html or "").lower()
    assert "Safe" in (html or "")
    assert features == [{"name": "Warranty", "value": "3 years", "position": 0}]
