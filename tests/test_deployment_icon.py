from pathlib import Path

import pytest
from jinja2 import Environment

from app import main


BASE_TEMPLATE = Path("app/templates/base.html")


@pytest.mark.parametrize(
    ("configured_slot", "expected_slot"),
    [("blue", "blue"), (" GREEN ", "green"), ("development", None), ("", None)],
)
def test_deployment_slot_is_normalised_and_restricted(monkeypatch, configured_slot, expected_slot):
    monkeypatch.setattr(main.settings, "app_instance_id", configured_slot)

    assert main._deployment_slot() == expected_slot


@pytest.mark.parametrize(
    ("slot", "colour"),
    [("blue", "#2563eb"), ("green", "#16a34a")],
)
def test_sidebar_icon_dot_reflects_deployment(slot, colour):
    source = BASE_TEMPLATE.read_text(encoding="utf-8")
    brand_fragment = source.split('<div class="brand">', 1)[1].split("</div>", 1)[0]
    template = Environment(autoescape=True).from_string(brand_fragment)

    html = template.render(app_name="MyPortal", deployment_slot=slot)

    assert f'data-deployment="{slot}"' in html
    assert f'fill="{colour}"' in html
    assert f"MyPortal — {slot.title()} deployment" in html
