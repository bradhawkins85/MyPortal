"""Rendered rack elevations keep physical position and free-space actions aligned."""

from pathlib import Path

import pytest
from jinja2 import Environment


TEMPLATE = (Path(__file__).resolve().parents[1] / "app/templates/infrastructure/racks.html").read_text()
ELEVATION = TEMPLATE[
    TEMPLATE.index('      <div class="rack-diagram-heading">'):
    TEMPLATE.index('      <details class="rack__details"')
]


@pytest.mark.parametrize(
    ("direction", "row"),
    [("bottom-up", 5), ("top-down", 3)],
)
def test_full_depth_item_is_one_spanning_block_on_each_face(direction, row):
    rack = {"id": 1, "name": "Core rack", "unit_count": 8, "numbering_direction": direction}
    item = {
        "id": 7, "rack_id": 1, "name": "Two U server", "asset_name": None,
        "item_type": "device", "start_unit": 3, "unit_height": 2,
        "start_lane": 1, "width_lanes": 3, "face": "front", "depth_mode": "full",
    }

    html = Environment(autoescape=True).from_string(ELEVATION).render(
        rack=rack, equipment=[item], reservations=[], can_edit=True
    )

    assert html.count('data-inspect-placement="7"') == 2
    assert html.count(f"grid-row: {row} / span 2; grid-column: 1 / span 3") == 2
    assert html.count("data-place-open") == 36
    assert 'aria-label="Edit Two U server, front, units 3 to 4, lanes 1 to 3"' in html


def test_reservation_occupies_only_its_face_and_lanes():
    rack = {"id": 1, "name": "Core rack", "unit_count": 4, "numbering_direction": "bottom-up"}
    reservation = {
        "id": 4, "rack_id": 1, "label": "Future switch", "start_unit": 2,
        "unit_height": 1, "start_lane": 2, "width_lanes": 2,
        "face": "rear", "depth_mode": "half",
    }

    html = Environment(autoescape=True).from_string(ELEVATION).render(
        rack=rack, equipment=[], reservations=[reservation], can_edit=True
    )

    assert html.count('data-inspect-reservation="4"') == 1
    assert 'grid-row: 3 / span 1; grid-column: 2 / span 2' in html
    assert html.count("data-place-open") == 22
