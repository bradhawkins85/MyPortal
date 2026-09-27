"""Generate the static rack faceplate SVGs in app/static/images/racks/.

Every rack item type gets a front and rear image at each lane width
(1/3, 2/3 and full), drawn with the type's default connections. The rack
view draws sides with documented connections dynamically from the same
drawers (app/services/rack_faceplates.py); these files serve the type
picker and items without connection data.

Run from the repository root:  python scripts/generate_rack_faceplates.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import rack_faceplates, rack_item_types  # noqa: E402

OUT = ROOT / "app" / "static" / "images" / "racks"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*.svg"):
        stale.unlink()
    written = 0
    for item_type in rack_item_types.ITEM_TYPES:
        for lanes in rack_faceplates.WIDTHS:
            for rear in (False, True):
                svg = rack_faceplates.render(item_type.key, lanes, item_type.image_units, rear)
                path = ROOT / "app" / rack_item_types.image_path(item_type.key, lanes, rear).lstrip("/")
                path.write_text(svg + "\n", encoding="utf-8")
                written += 1
    print(f"Wrote {written} faceplates to {OUT}")


if __name__ == "__main__":
    main()
