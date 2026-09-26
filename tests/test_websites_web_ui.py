from __future__ import annotations

from starlette.datastructures import FormData
from fastapi import HTTPException
import pytest

from app.features.websites.routes import _form_payload


def test_website_form_maps_links_and_monitoring_options():
    payload = _form_payload(FormData([
        ("name", "Customer portal"), ("url", "https://example.com"),
        ("monitor_availability", "on"), ("asset_ids", "4"), ("asset_ids", "7"),
        ("knowledge_base_article_ids", "12"),
    ]))

    assert payload.name == "Customer portal"
    assert payload.monitor_availability is True
    assert payload.monitor_tls is False
    assert payload.asset_ids == [4, 7]
    assert payload.knowledge_base_article_ids == [12]


def test_website_form_rejects_invalid_link_identifier():
    with pytest.raises(HTTPException) as exc:
        _form_payload(FormData([
            ("name", "Portal"), ("url", "https://example.com"), ("asset_ids", "other-company"),
        ]))

    assert exc.value.status_code == 422
    assert exc.value.detail == "Invalid linked record"
