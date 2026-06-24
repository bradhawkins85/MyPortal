import asyncio

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


def test_improve_product_description_waits_for_ai_response(monkeypatch):
    calls = []

    async def fake_get_product_by_id(product_id, include_archived=False, **_kwargs):
        assert product_id == 44
        assert include_archived is True
        return {"id": product_id, "description": "CPU: Intel i7"}

    async def fake_trigger_module(slug, payload, **kwargs):
        calls.append((slug, payload, kwargs))
        return {
            "status": "succeeded",
            "response": {
                "response": (
                    '{"description_html":"<h3>Specifications</h3><p>CPU: Intel i7</p>",'
                    '"features":[{"name":"CPU","value":"Intel i7"}]}'
                )
            },
        }

    async def fake_update_product_description(product_id, description):
        return {"id": product_id, "description": description}

    replaced = {}

    async def fake_replace_product_features(product_id, features):
        replaced["product_id"] = product_id
        replaced["features"] = features

    monkeypatch.setattr(
        product_descriptions.shop_repo, "get_product_by_id", fake_get_product_by_id
    )
    monkeypatch.setattr(
        product_descriptions.modules_service, "trigger_module", fake_trigger_module
    )
    monkeypatch.setattr(
        product_descriptions.shop_repo,
        "update_product_description",
        fake_update_product_description,
    )
    monkeypatch.setattr(
        product_descriptions.shop_repo,
        "replace_product_features",
        fake_replace_product_features,
    )

    result = asyncio.run(product_descriptions.improve_product_description(44))

    assert calls[0][0] == "ollama"
    assert calls[0][2]["background"] is False
    assert result["description"] == "<h3>Specifications</h3><p>CPU: Intel i7</p>"
    assert replaced["features"] == [{"name": "CPU", "value": "Intel i7", "position": 0}]
