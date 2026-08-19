"""Download and import the IEEE OUI vendor registry."""
from __future__ import annotations
import csv
import io
import re
import httpx
from app.repositories import mac_vendors as mac_vendors_repo

IEEE_OUI_CSV_URL = "https://standards-oui.ieee.org/oui/oui.csv"
_OUI_RE = re.compile(r"^[0-9A-F]{6}$")

def parse_ieee_oui_csv(content: str) -> list[tuple[str, str]]:
    """Return unique, normalized ``(OUI, vendor)`` rows from IEEE CSV data."""
    assignments: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(content.lstrip("\ufeff"))):
        prefix = re.sub(r"[^0-9A-F]", "", (row.get("Assignment") or "").upper())
        vendor = (row.get("Organization Name") or "").strip()
        if _OUI_RE.fullmatch(prefix) and vendor:
            assignments[prefix] = vendor
    if not assignments:
        raise ValueError("IEEE OUI download did not contain any valid assignments")
    return sorted(assignments.items())

async def update_mac_vendors() -> dict[str, int | str]:
    """Fetch the current IEEE registry and replace the database lookup list."""
    headers = {"User-Agent": "MyPortal MAC vendor updater/1.0"}
    async with httpx.AsyncClient(
        timeout=60.0, follow_redirects=True, headers=headers
    ) as client:
        response = await client.get(IEEE_OUI_CSV_URL)
        response.raise_for_status()
    assignments = parse_ieee_oui_csv(response.text)
    imported = await mac_vendors_repo.replace_all(assignments)
    return {"source": IEEE_OUI_CSV_URL, "imported": imported}
