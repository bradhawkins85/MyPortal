"""Regenerate app/static/images/asset-types/*.svg from the asset type catalogue.

Run from anywhere: ``python scripts/generate_asset_type_icons.py``. Each icon is
a rounded tile in its category colour with a white line glyph. Icons carry no
element ids so they can be embedded into network map SVGs as ``<symbol>``s.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import asset_types as t  # noqa: E402

S='stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"'
F='fill="#fff"'
S15=S.replace('stroke-width="2"','stroke-width="1.5"')
G = {
 "modem": f'<rect x="10" y="22" width="28" height="12" rx="2" {S}/><path d="M16 22 13 12M32 22l3-10" {S}/><circle cx="16" cy="28" r="1.5" {F}/><circle cx="21" cy="28" r="1.5" {F}/><circle cx="26" cy="28" r="1.5" {F}/>',
 "router": f'<rect x="9" y="24" width="30" height="11" rx="2" {S}/><path d="M17 24v-8M31 24v-8M20 14a6 6 0 0 1 8 0" {S}/><circle cx="15" cy="29.5" r="1.5" {F}/><circle cx="20" cy="29.5" r="1.5" {F}/><path d="M26 29.5h8" {S}/>',
 "firewall": f'<rect x="10" y="12" width="28" height="24" rx="1" {S}/><path d="M10 20h28M10 28h28M19 12v8M29 20v8M19 28v8" {S}/>',
 "vpn_gateway": f'<rect x="14" y="22" width="20" height="14" rx="2" {S}/><path d="M18 22v-4a6 6 0 0 1 12 0v4" {S}/><circle cx="24" cy="29" r="2" {F}/>',
 "switch": f'<rect x="7" y="17" width="34" height="14" rx="2" {S}/><path d="M12 22h3v4h-3zM18 22h3v4h-3zM24 22h3v4h-3zM30 22h3v4h-3z" {S15}/><circle cx="37" cy="24" r="1.2" {F}/>',
 "patch_panel": f'<rect x="7" y="18" width="34" height="12" rx="1.5" {S}/><path d="M11 22h3v4h-3zM17 22h3v4h-3zM23 22h3v4h-3zM29 22h3v4h-3zM35 22h2v4h-2z" {S15}/><path d="M12 30v6M18 30v4M24 30v6M30 30v4" {S15}/>',
 "load_balancer": f'<circle cx="12" cy="24" r="3" {S}/><path d="M15 24h6M21 24l12-9M21 24l12 0M21 24l12 9" {S}/><circle cx="36" cy="15" r="2.5" {S}/><circle cx="36" cy="24" r="2.5" {S}/><circle cx="36" cy="33" r="2.5" {S}/>',
 "access_point": f'<ellipse cx="24" cy="32" rx="12" ry="4" {S}/><path d="M17 20a10 10 0 0 1 14 0M20 24a5 5 0 0 1 8 0" {S}/><circle cx="24" cy="32" r="1.5" {F}/>',
 "wireless_bridge": f'<path d="M10 14c5 3 5 13 0 16M10 14v16" {S}/><path d="M38 14c-5 3-5 13 0 16M38 14v16" {S}/><path d="M17 22h14" {S} stroke-dasharray="2 3"/><path d="M10 30v6M38 30v6M7 36h6M35 36h6" {S}/>',
 "wireless_controller": f'<rect x="9" y="26" width="30" height="10" rx="2" {S}/><path d="M16 19a11 11 0 0 1 16 0M19.5 22.5a6 6 0 0 1 9 0" {S}/><circle cx="14" cy="31" r="1.5" {F}/><path d="M20 31h14" {S}/>',
 "server": f'<rect x="12" y="9" width="24" height="9" rx="1.5" {S}/><rect x="12" y="19.5" width="24" height="9" rx="1.5" {S}/><rect x="12" y="30" width="24" height="9" rx="1.5" {S}/><circle cx="17" cy="13.5" r="1.3" {F}/><circle cx="17" cy="24" r="1.3" {F}/><circle cx="17" cy="34.5" r="1.3" {F}/><path d="M23 13.5h9M23 24h9M23 34.5h9" {S}/>',
 "hypervisor": f'<rect x="10" y="26" width="28" height="11" rx="1.5" {S}/><rect x="12" y="11" width="10" height="10" rx="1" {S}/><rect x="26" y="11" width="10" height="10" rx="1" {S}/><path d="M17 21v5M31 21v5" {S}/><circle cx="15" cy="31.5" r="1.3" {F}/>',
 "virtual_machine": f'<rect x="10" y="12" width="28" height="20" rx="2" {S} stroke-dasharray="4 3"/><path d="M18 18l6 8 6-8" {S}/><path d="M18 37h12" {S}/>',
 "storage": f'<ellipse cx="24" cy="13" rx="12" ry="4" {S}/><path d="M12 13v22c0 2.2 5.4 4 12 4s12-1.8 12-4V13M12 24c0 2.2 5.4 4 12 4s12-1.8 12-4" {S}/>',
 "workstation": f'<rect x="8" y="10" width="32" height="21" rx="2" {S}/><path d="M20 31l-2 6h12l-2-6M15 37h18" {S}/>',
 "laptop": f'<rect x="12" y="12" width="24" height="17" rx="1.5" {S}/><path d="M7 33h34l-3 4H10z" {S}/>',
 "thin_client": f'<rect x="8" y="11" width="22" height="16" rx="2" {S}/><rect x="33" y="14" width="7" height="22" rx="1.5" {S}/><path d="M19 27v5M14 32h10" {S}/>',
 "tablet": f'<rect x="13" y="8" width="22" height="32" rx="3" {S}/><circle cx="24" cy="35" r="1.3" {F}/>',
 "mobile_phone": f'<rect x="16" y="8" width="16" height="32" rx="3" {S}/><path d="M21 12h6" {S}/><circle cx="24" cy="35" r="1.3" {F}/>',
 "printer": f'<path d="M15 18V9h18v9" {S}/><rect x="9" y="18" width="30" height="13" rx="2" {S}/><path d="M15 27h18v11H15z" {S}/><circle cx="34" cy="22.5" r="1.3" {F}/>',
 "scanner": f'<rect x="8" y="22" width="32" height="12" rx="2" {S}/><path d="M11 22l4-8h22l-4 8M14 28h20" {S}/>',
 "ip_phone": f'<rect x="10" y="20" width="28" height="18" rx="2" {S}/><path d="M13 20c0-6 22-6 22 0" {S}/><rect x="15" y="24" width="10" height="5" rx="1" {S15}/><circle cx="30" cy="25" r="1" {F}/><circle cx="34" cy="25" r="1" {F}/><circle cx="30" cy="30" r="1" {F}/><circle cx="34" cy="30" r="1" {F}/>',
 "phone_system": f'<rect x="10" y="10" width="28" height="28" rx="2" {S}/><path d="M18 18c0 7 5 12 12 12l2-3-4-2-2 2c-2-1-4-3-5-5l2-2-2-4z" {S}/>',
 "display": f'<rect x="7" y="10" width="34" height="22" rx="2" {S}/><path d="M24 32v5M16 37h16" {S}/>',
 "conference": f'<ellipse cx="24" cy="30" rx="14" ry="6" {S}/><circle cx="24" cy="14" r="4" {S}/><path d="M24 18v6" {S}/><circle cx="24" cy="30" r="2" {F}/>',
 "camera": f'<path d="M9 16l22 4-2 9-22-4z" {S}/><path d="M31 20l6-3v11l-6-3M18 26v6M12 36h12" {S}/>',
 "nvr": f'<rect x="8" y="18" width="32" height="14" rx="2" {S}/><circle cx="16" cy="25" r="3.5" {S}/><circle cx="16" cy="25" r="1" {F}/><path d="M24 23h11M24 27h7" {S}/>',
 "access_control": f'<rect x="14" y="8" width="20" height="32" rx="2" {S}/><circle cx="24" cy="18" r="4" {S}/><path d="M19 28h10M19 33h10" {S}/>',
 "ups": f'<rect x="12" y="8" width="24" height="32" rx="2" {S}/><path d="M26 14l-6 10h8l-6 10" {S}/>',
 "pdu": f'<rect x="18" y="6" width="12" height="36" rx="2" {S}/><path d="M22 12v3M26 12v3M22 21v3M26 21v3M22 30v3M26 30v3" {S}/>',
 "iot": f'<rect x="16" y="16" width="16" height="16" rx="2" {S}/><path d="M20 16v-5M28 16v-5M20 32v5M28 32v5M16 20h-5M16 28h-5M32 20h5M32 28h5" {S}/><circle cx="24" cy="24" r="2" {F}/>',
 "cloud_service": f'<path d="M15 34a7 7 0 0 1-1-13.9A9 9 0 0 1 31.5 17 7.5 7.5 0 0 1 33 34z" {S}/>',
 "other": f'<rect x="11" y="11" width="26" height="26" rx="4" {S}/><path d="M20 20a4 4 0 1 1 5 3.9c-1 .4-1 1.1-1 2.1" {S}/><circle cx="24" cy="31" r="1.4" {F}/>',
}
assert set(G)==set(t.BY_KEY), set(t.BY_KEY)^set(G)
for key,glyph in G.items():
    c=t.get(key).colour
    svg=(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="48" height="48" role="img" aria-label="{t.get(key).label}">'
         f'<rect x="1" y="1" width="46" height="46" rx="10" fill="{c}"/>{glyph}</svg>\n')
    (ROOT / 'app/static/images/asset-types' / f'{key}.svg').open('w').write(svg)
print(f'Wrote {len(G)} icons')
