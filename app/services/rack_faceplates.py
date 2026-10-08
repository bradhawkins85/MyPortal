"""SVG faceplates for rack items.

``render`` draws one side of a rack item. Sides that carry documented
connections (network ports, power supplies, outlets, KVM device ports) are
drawn from the item's actual connections: every port is shown, connected
ones with a plugged cable and a lit LED, and the layout spans the item's
full height, so a 2U server with two PSUs shows two PSUs rather than two
per unit. Other sides (a server's drive bays, a shelf) are decorative.

``scripts/generate_rack_faceplates.py`` uses the same drawers with each
type's default connections to write the static images used by the type
picker and for items without connection data.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from urllib.parse import quote

from app.services import rack_item_types

UNIT = 44  # 1U height in viewBox units
WIDTHS = {1: 160, 2: 320, 3: 480}
LED_GREEN, LED_BLUE, LED_AMBER, LED_RED = "#34d399", "#38bdf8", "#fbbf24", "#d03b3b"
LED_OFF = "#3a4150"
METAL = "#8a94a3"
CABLE_DATA, CABLE_POWER = "#2f6fd6", "#15181d"

Connections = Mapping[str, list[bool]]


class Face:
    """A drawing surface for one faceplate."""

    def __init__(self, width: int, height: int, lanes: int, top: str, bottom: str,
                 rim: str = "#5b6574", ear: str = "#2b323c"):
        self.w, self.h, self.lanes = width, height, lanes
        self.parts: list[str] = []
        self.defs = (f'<linearGradient id="b" x1="0" y1="0" x2="0" y2="1">'
                     f'<stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/></linearGradient>')
        if lanes == 3:
            for x in (0, width - 16):
                self.rect(x, 0, 16, height, ear)
                for unit in range(max(1, height // UNIT)):
                    self.rect(x + 5, unit * UNIT + 8, 6, 5, "#0b0e12", rx=2)
                    self.rect(x + 5, unit * UNIT + 31, 6, 5, "#0b0e12", rx=2)
            self.x0, self.x1 = 16, width - 16
            self.parts.append(f'<rect x="16" y="0" width="{width - 32}" height="{height}" fill="url(#b)"/>')
            self.parts.append(f'<rect x="16.5" y=".5" width="{width - 33}" height="{height - 1}" fill="none" stroke="{rim}" stroke-opacity=".5"/>')
        else:
            # Shelf-mounted unit: no ears, rounded chassis with a small gap.
            self.x0, self.x1 = 4, width - 4
            self.parts.append(f'<rect x="4" y="2" width="{width - 8}" height="{height - 4}" rx="3" fill="url(#b)" stroke="{rim}" stroke-opacity=".6"/>')
        self.inner_x0, self.inner_x1 = self.x0 + 8, self.x1 - 8
        self.top, self.bottom = 5, height - 5

    @property
    def span(self) -> float:
        return self.inner_x1 - self.inner_x0

    def rect(self, x, y, w, h, fill, rx=0, stroke=None, sw=0.8, opacity=None):
        attrs = f'x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{max(h, 0):.1f}" fill="{fill}"'
        if rx:
            attrs += f' rx="{rx:.1f}"'
        if stroke:
            attrs += f' stroke="{stroke}" stroke-width="{sw:g}"'
        if opacity is not None:
            attrs += f' opacity="{opacity:g}"'
        self.parts.append(f"<rect {attrs}/>")

    def circle(self, cx, cy, r, fill="none", stroke=None, sw=1.0):
        extra = f' stroke="{stroke}" stroke-width="{sw:g}"' if stroke else ""
        self.parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}"{extra}/>')

    def path(self, d, fill="none", stroke=None, sw=1.0, opacity=None):
        extra = f' stroke="{stroke}" stroke-width="{sw:g}"' if stroke else ""
        if opacity is not None:
            extra += f' opacity="{opacity:g}"'
        self.parts.append(f'<path d="{d}" fill="{fill}"{extra}/>')

    def svg(self) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" preserveAspectRatio="none">'
                f"<defs>{self.defs}</defs>{''.join(self.parts)}</svg>")


# ------------------------------------------------------------------ layout
def fit(span: float, pitch: float, minimum: int = 1, maximum: int = 999) -> int:
    return max(minimum, min(maximum, int(span // pitch)))


def grid(box: tuple[float, float, float, float], count: int, w: float, h: float,
         gap_x: float = 2, gap_y: float = 4, max_rows: int | None = None,
         max_scale: float = 1.0) -> tuple[list[tuple[float, float]], float]:
    """Place ``count`` items of size ``w``×``h`` in a box, shrinking them to fit.

    Returns top-left positions and the scale applied. Rows are balanced so a
    run of ports fills evenly (e.g. 8 ports in 2 rows of 4).
    """
    if count <= 0:
        return [], max_scale
    x0, y0, x1, y1 = box
    scale = max_scale
    while True:
        cw, ch, gx, gy = w * scale, h * scale, gap_x * scale, gap_y * scale
        cols_fit = max(1, int((x1 - x0 + gx) // (cw + gx)))
        rows_fit = max(1, int((y1 - y0 + gy) // (ch + gy)))
        if max_rows:
            rows_fit = min(rows_fit, max_rows)
        if cols_fit * rows_fit >= count or scale < 0.35:
            break
        scale *= 0.88
    rows = min(rows_fit, math.ceil(count / cols_fit))
    cols = math.ceil(count / rows)
    total_h = rows * ch + (rows - 1) * gy
    origin_y = y0 + max(0, (y1 - y0 - total_h) / 2)
    positions = [(x0 + (i % cols) * (cw + gx), origin_y + (i // cols) * (ch + gy)) for i in range(count)]
    return positions, scale


def span_width(count: int, w: float, gap: float, rows: int) -> float:
    cols = math.ceil(count / max(rows, 1)) if count else 0
    return cols * w + max(cols - 1, 0) * gap


# -------------------------------------------------------------- components
def rj45(f: Face, x, y, s=1.0, linked=False, frame=METAL):
    w, h = 11 * s, 9 * s
    f.rect(x, y + 2.4 * s, w, h, "#050608", rx=1 * s, stroke=frame, sw=0.8 * s)
    f.rect(x + w / 2 - 2.5 * s, y + 2.4 * s + h - 3 * s, 5 * s, 3 * s, frame, opacity=0.6)
    f.rect(x + 1 * s, y, 3 * s, 1.6 * s, LED_GREEN if linked else LED_OFF)
    if linked:
        f.rect(x + 1.5 * s, y + 3.6 * s, w - 3 * s, h - 2.4 * s, CABLE_DATA, rx=1 * s)


def keystone(f: Face, x, y, s=1.0, linked=False):
    f.rect(x, y, 12 * s, 5 * s, "#e5e7eb", opacity=0.85)
    f.rect(x, y + 8 * s, 12 * s, 16 * s, "#040506", rx=1 * s, stroke="#a4acb8", sw=0.8 * s)
    f.rect(x + 3.5 * s, y + 21 * s, 5 * s, 3 * s, "#a4acb8", opacity=0.6)
    if linked:
        f.rect(x + 2 * s, y + 10 * s, 8 * s, 11 * s, CABLE_DATA, rx=1 * s)


def iec_outlet(f: Face, x, y, s=1.0, linked=False):
    """C13/C19 style outlet; a connected outlet shows an inserted plug."""
    w, h = 26 * s, 20 * s
    f.path(f"M{x:.1f} {y:.1f}h{w:.1f}v{h * .72:.1f}l-{4 * s:.1f} {h * .28:.1f}h-{w - 8 * s:.1f}l-{4 * s:.1f}-{h * .28:.1f}z",
           fill="#050608", stroke="#a4acb8", sw=s)
    if linked:
        f.rect(x + 3 * s, y + 3 * s, w - 6 * s, h - 6 * s, "#262b33", rx=2 * s)
        f.rect(x + w / 2 - 2 * s, y + h - 4 * s, 4 * s, 6 * s, CABLE_POWER)
    else:
        for dx in (6, 11.5, 17):
            f.rect(x + dx * s, y + 6 * s, 2.6 * s, 7 * s, "#a4acb8")
    f.rect(x + 10 * s, y + h + 2 * s, 6 * s, 2 * s, LED_GREEN if linked else LED_OFF)


def three_pin_outlet(f: Face, x, y, s=1.0, linked=False):
    """AU/NZ style 3-pin socket; a connected socket shows an inserted plug."""
    w = 26 * s
    f.rect(x, y, w, 22 * s, "#e5e7eb", rx=3 * s, stroke="#9aa3ae", sw=s)
    if linked:
        f.rect(x + 4 * s, y + 4 * s, w - 8 * s, 14 * s, "#f8fafc", rx=2 * s, stroke="#9aa3ae", sw=s)
        f.rect(x + w / 2 - 2 * s, y + 16 * s, 4 * s, 8 * s, "#e5e7eb")
    else:
        f.path(f"M{x + 7 * s:.1f} {y + 6 * s:.1f}l{3 * s:.1f} {5 * s:.1f}", stroke="#111418", sw=2.2 * s)
        f.path(f"M{x + 19 * s:.1f} {y + 6 * s:.1f}l-{3 * s:.1f} {5 * s:.1f}", stroke="#111418", sw=2.2 * s)
        f.path(f"M{x + 13 * s:.1f} {y + 13 * s:.1f}v{6 * s:.1f}", stroke="#111418", sw=2.2 * s)
    f.circle(x + 22 * s, y + 18 * s, 1.4 * s, LED_GREEN if linked else LED_OFF)


def iec_inlet(f: Face, x, y, s=1.0, fed=False):
    f.rect(x, y, 22 * s, 16 * s, "#050608", rx=2 * s, stroke="#a4acb8", sw=s)
    if fed:
        f.rect(x + 3 * s, y + 3 * s, 16 * s, 10 * s, "#262b33", rx=2 * s)
        f.rect(x + 9 * s, y + 13 * s, 4 * s, 9 * s, CABLE_POWER)
    else:
        for dx in (5, 10, 15):
            f.rect(x + dx * s, y + 5 * s, 2 * s, 6 * s, "#a4acb8")


def psu(f: Face, x, y, w, h, fed=False):
    """Hot-swap power supply with an IEC inlet; a fed PSU shows its cord."""
    f.rect(x, y, w, h, "#1b2027", rx=2, stroke="#6c7686")
    s = min(1.0, (h - 6) / 16)
    iec_inlet(f, x + 4, y + (h - 16 * s) / 2, s, fed)
    grille_x = x + 8 + 22 * s
    for k in range(max(0, int((w - (grille_x - x) - 10) // 5))):
        f.rect(grille_x + k * 5, y + 5, 3, h - 10, "#0b0e12", rx=1)
    f.rect(x + w - 8, y + 5, 4, 4, LED_GREEN if fed else LED_AMBER)


def kvm_port(f: Face, x, y, s=1.0, linked=False):
    """One KVM channel: VGA plus two USB; a connected channel shows its cable."""
    f.path(f"M{x:.1f} {y:.1f}h{24 * s:.1f}l-{3 * s:.1f} {9 * s:.1f}h-{18 * s:.1f}z", fill="#0b3b8c", stroke="#6c7686", sw=s)
    f.rect(x + 2 * s, y + 13 * s, 8 * s, 8 * s, "#0d1a2c", stroke="#6c7686", sw=s)
    f.rect(x + 14 * s, y + 13 * s, 8 * s, 8 * s, "#0d1a2c", stroke="#6c7686", sw=s)
    if linked:
        f.rect(x + 5 * s, y + 1.5 * s, 14 * s, 6 * s, "#1e3a8a", rx=1 * s)
        f.rect(x + 3.5 * s, y + 14.5 * s, 5 * s, 5 * s, "#e5e7eb")
    f.rect(x + 22 * s, y + 23 * s, 2 * s, 2 * s, LED_GREEN if linked else LED_OFF)


def fan(f: Face, cx, cy, r):
    f.circle(cx, cy, r, "#07090b", "#6c7686")
    for ring in (r * .35, r * .65):
        f.circle(cx, cy, ring, "none", "#4c5563", 0.8)
    f.path(f"M{cx - r:.1f} {cy:.1f}h{2 * r:.1f}M{cx:.1f} {cy - r:.1f}v{2 * r:.1f}", stroke="#4c5563", sw=0.8)
    f.circle(cx, cy, r * .18, "#6c7686")


def vents(f: Face, x, y, w, h, pitch=5):
    for k in range(max(0, int(w // pitch))):
        f.rect(x + k * pitch, y, pitch - 2, h, "#101419", rx=1)


def drive_bay(f: Face, x, y, w, h):
    f.rect(x, y, w, h, "#15191f", rx=2, stroke="#6c7686")
    f.rect(x + 3, y + 4, w - 6, h * .55, "#232932")
    for k in range(max(1, int((w - 8) // 4))):
        f.rect(x + 4 + k * 4, y + 6, 2, h * .45, "#11151a")
    f.rect(x + 4, y + h - 7, 4, 3, LED_GREEN)
    f.rect(x + 10, y + h - 7, 4, 3, LED_BLUE, opacity=0.8)


# ------------------------------------------------------ connection panels
def draw_data_ports(f: Face, box, links: list[bool], max_rows=None):
    """RJ45 ports; by default two rows per rack unit, like a switch."""
    rows = max_rows if max_rows is not None else 2 * max(1, round(f.h / UNIT))
    positions, scale = grid(box, len(links), 11, 12, gap_x=2, gap_y=4, max_rows=rows)
    for (x, y), linked in zip(positions, links):
        rj45(f, x, y, scale, linked)


def draw_psus(f: Face, box, fed: list[bool], module_w=64):
    x0, y0, x1, y1 = box
    rows = max(1, min(len(fed), int((y1 - y0 + 4) // 38)))
    positions, scale = grid(box, len(fed), module_w, 34, gap_x=4, gap_y=4, max_rows=rows)
    for (x, y), is_fed in zip(positions, fed):
        psu(f, x, y, module_w * scale, 34 * scale, is_fed)


def psu_width(count: int, height: float, module_w=64) -> float:
    rows = max(1, min(count, int((height + 4) // 38)))
    return span_width(count, module_w, 4, rows)


def draw_outlets(f: Face, box, iec: list[bool], three: list[bool]):
    """IEC outlets then 3-pin sockets, each in its own balanced block."""
    x0, y0, x1, y1 = box
    total = len(iec) + len(three)
    if not total:
        return
    split = x0 + (x1 - x0) * (len(iec) / total) if iec and three else (x1 if iec else x0)
    if iec:
        positions, scale = grid((x0, y0, split - (4 if three else 0), y1), len(iec), 26, 24, gap_x=6, gap_y=4)
        for (x, y), linked in zip(positions, iec):
            iec_outlet(f, x, y, scale, linked)
    if three:
        positions, scale = grid((split, y0, x1, y1), len(three), 26, 24, gap_x=6, gap_y=4)
        for (x, y), linked in zip(positions, three):
            three_pin_outlet(f, x, y, scale, linked)


def draw_inputs(f: Face, x1, fed: list[bool], y0=None, y1=None) -> float:
    """Power input inlets along the right edge; returns their left edge."""
    if not fed:
        return x1
    y0 = f.top + 4 if y0 is None else y0
    y1 = f.bottom - 4 if y1 is None else y1
    positions, scale = grid((x1 - 26 * len(fed), y0, x1, y1), len(fed), 22, 22, gap_x=4, gap_y=4)
    for (x, y), is_fed in zip(positions, fed):
        iec_inlet(f, x, y, scale, is_fed)
    return min(x for x, _y in positions) - 6


# ------------------------------------------------------------------- types
def server(f: Face, rear: bool, conns: Connections) -> None:
    if not rear:
        rows = max(1, round(f.h / UNIT))
        bays = fit(f.span * (0.6 if f.lanes > 1 else 0.7), 34, 1, 8)
        for row in range(rows):
            for i in range(bays):
                drive_bay(f, f.inner_x0 + i * 34, 6 + row * UNIT, 31, 32)
            rest = f.inner_x0 + bays * 34 + 4
            vents(f, rest, 9 + row * UNIT, max(5, f.inner_x1 - rest - 36), 26)
        f.rect(f.inner_x1 - 30, 12, 26, 20, "#1b2027", rx=3, stroke="#6c7686")
        f.circle(f.inner_x1 - 17, 22, 5, "none", LED_BLUE, 1.6)
        return
    psus, ports = conns.get("psu", []), conns.get("data", [])
    height = f.bottom - f.top
    psu_w = min(f.span * 0.5, psu_width(len(psus), height))
    draw_psus(f, (f.inner_x1 - psu_w, f.top, f.inner_x1, f.bottom), psus)
    io_x1 = f.inner_x1 - psu_w - 8
    # Network ports sit in a block of at most two rows, like a server's NIC area.
    port_w = min(io_x1 - f.inner_x0, max(span_width(len(ports), 13, 0, 2), 13))
    draw_data_ports(f, (f.inner_x0, f.top, f.inner_x0 + port_w, min(f.bottom, f.top + 34)), ports, max_rows=2)
    x = f.inner_x0 + port_w + 8
    if io_x1 - x > 30:
        f.rect(x, f.top + 4, 7, 9, "#0d1a2c", stroke="#6c7686")  # USB
        f.path(f"M{x + 10} {f.top + 4}h22l-3 9h-16z", fill="#0b3b8c", stroke="#6c7686")  # VGA
        for k in range(fit(io_x1 - x, 30, 0, 4)):
            f.rect(x + k * 30, f.bottom - 18, 26, 16, "#1b2027", rx=1, stroke="#4c5563")
            vents(f, x + k * 30 + 3, f.bottom - 15, 20, 10, 4)


def switch(f: Face, rear: bool, conns: Connections) -> None:
    if not rear:
        ports = conns.get("data", [])
        f.rect(f.inner_x0, 8, 16, 28, "#10261e", rx=2)
        for k, colour in enumerate((LED_GREEN, LED_GREEN, LED_AMBER, LED_GREEN)):
            f.rect(f.inner_x0 + 4, 12 + k * 6, 8, 3, colour)
        console_x = f.inner_x1 - 12
        f.rect(console_x, 15, 10, 14, "#050608", rx=1, stroke=LED_BLUE)
        draw_data_ports(f, (f.inner_x0 + 22, f.top, console_x - 6, f.bottom), ports)
        return
    psus = conns.get("psu", [])
    psu_w = min(f.span * 0.6, psu_width(len(psus), f.bottom - f.top, 70))
    draw_psus(f, (f.inner_x1 - psu_w, f.top, f.inner_x1, f.bottom), psus, 70)
    fans_x1 = f.inner_x1 - psu_w - 6
    for k in range(fit(fans_x1 - f.inner_x0 - 22, 40, 0, 4)):
        fan(f, f.inner_x0 + 22 + k * 40 + 17, f.h / 2, 15)
    rj45(f, f.inner_x0, 8, frame=LED_BLUE)  # console
    rj45(f, f.inner_x0, 24)  # management


def storage(f: Face, rear: bool, conns: Connections) -> None:
    if not rear:
        rows = max(3, round(f.h / UNIT) * 3 // 2)
        panel = 36 if f.lanes > 1 else 0
        cols = fit(f.span - panel - 4, 98 if f.lanes > 1 else 70, 1, 4)
        sled_w = (f.span - panel - 4) / cols - 4
        sled_h = (f.h - 12) / rows - 3
        for row in range(rows):
            for col in range(cols):
                x, y = f.inner_x0 + col * (sled_w + 4), 6 + row * (sled_h + 3)
                f.rect(x, y, sled_w, sled_h, "#141a24", rx=2, stroke="#7c90b3")
                for k in range(max(1, int((sled_w - 30) // 6))):
                    f.rect(x + 6 + k * 6, y + 5, 3, sled_h - 10, "#0a0e14")
                f.rect(x + sled_w - 8, y + 5, 4, 4, LED_GREEN)
                f.rect(x + sled_w - 8, y + sled_h - 9, 4, 4, LED_BLUE)
        if panel:
            px = f.inner_x1 - panel
            f.rect(px, 6, panel, f.h - 12, "#10151e", rx=3, stroke="#7c90b3")
            f.rect(px + 6, 14, panel - 12, 10, "#0e2a3a")
            f.rect(px + 8, 17, panel - 20, 3, LED_BLUE)
        return
    psus, ports = conns.get("psu", []), conns.get("data", [])
    psu_w = min(f.span * 0.4, psu_width(len(psus), f.bottom - f.top, 70))
    draw_psus(f, (f.inner_x1 - psu_w, f.top, f.inner_x1, f.bottom), psus, 70)
    ctrl_x1 = f.inner_x1 - psu_w - 6
    controllers = 2 if f.lanes > 1 and len(ports) > 1 else 1
    ctrl_w = (ctrl_x1 - f.inner_x0) / controllers - 6
    share = math.ceil(len(ports) / controllers)
    for c in range(controllers):
        cx = f.inner_x0 + c * (ctrl_w + 6)
        f.rect(cx, f.top, ctrl_w, f.bottom - f.top, "#141a24", rx=2, stroke="#7c90b3")
        mine = ports[c * share:(c + 1) * share]
        draw_data_ports(f, (cx + 6, f.top + 4, cx + ctrl_w - 6, f.top + (f.bottom - f.top) * 0.55), mine, max_rows=1)
        fan(f, cx + ctrl_w / 2, f.top + (f.bottom - f.top) * 0.75, min(12, (f.bottom - f.top) * 0.18))


def patch_panel(f: Face, rear: bool, conns: Connections) -> None:
    ports = conns.get("data", [])
    if not rear:
        positions, scale = grid((f.inner_x0, f.top, f.inner_x1, f.bottom), len(ports), 12, 24, gap_x=4, gap_y=4,
                                max_rows=max(1, round(f.h / UNIT)))
        for (x, y), linked in zip(positions, ports):
            keystone(f, x, y, scale, linked)
        return
    groups = fit(f.span, 106, 1, 4)
    per_group = 6 if f.lanes > 1 else fit(f.span - 8, 16, 2, 6)
    group_w = per_group * 16 + 2
    gap = (f.span - groups * group_w) / max(groups, 1)
    for g in range(groups):
        gx = f.inner_x0 + gap / 2 + g * (group_w + gap)
        f.rect(gx - 4, 6, group_w + 4, 22, "#0f1215", rx=2)
        for col in range(per_group):
            x = gx + col * 16
            f.rect(x, 9, 12, 16, "#1f252d", rx=1, stroke="#6c7686")
            f.rect(x + 3, 12, 6, 10, CABLE_DATA, opacity=0.8)
        f.rect(gx - 4, 32, group_w + 4, 4, "#9aa3ae", rx=2)


def kvm(f: Face, rear: bool, conns: Connections) -> None:
    if not rear:
        x0, x1 = f.inner_x0, f.inner_x1
        f.rect(x0, 5, f.span, 12, "#0d1116", rx=2, stroke="#6c7686", sw=0.6)
        f.rect(x0 + f.span * .3, 8, f.span * .38, 6, "#0e2a3a")
        f.rect(x0 + f.span * .31, 9.5, f.span * .12, 3, LED_BLUE, opacity=0.8)
        handle = 26 if f.lanes > 1 else 16
        keys = fit(f.span - 2 * handle - 20, 14, 4, 26)
        start = x0 + handle + 8 + ((f.span - 2 * handle - 16) - keys * 14) / 2
        for row in range(2):
            for k in range(keys):
                f.rect(start + k * 14, 21 + row * 7, 11, 5, "#1f252d", rx=1, stroke="#4c5563", sw=0.5)
        f.rect(x0, 20, handle, 20, "#252b33", rx=3, stroke=METAL, sw=1)
        f.rect(x1 - handle, 20, handle, 20, "#252b33", rx=3, stroke=METAL, sw=1)
        return
    devices, ports, psus = conns.get("kvm", []), conns.get("data", []), conns.get("psu", [])
    inputs_x = draw_inputs(f, f.inner_x1, psus)
    net_w = span_width(len(ports), 13, 0, 1) if ports else 0
    draw_data_ports(f, (inputs_x - net_w, f.top, inputs_x, f.bottom), ports, max_rows=1)
    positions, scale = grid((f.inner_x0, f.top, inputs_x - net_w - 8, f.bottom), len(devices), 24, 26, gap_x=6, gap_y=3)
    for (x, y), linked in zip(positions, devices):
        kvm_port(f, x, y, scale, linked)


def pdu(f: Face, rear: bool, conns: Connections) -> None:
    if not rear:
        x = f.inner_x0
        if f.lanes > 1:
            f.rect(x, 8, 46, 28, "#0b0d10", rx=2, stroke="#6c7686")
            f.rect(x + 5, 14, 36, 16, "#061a12")
            for k in range(3):
                f.rect(x + 9 + k * 10, 21, 7, 2, LED_GREEN, opacity=0.8)
            f.rect(x + 54, 10, 20, 24, "#1f2226", rx=2, stroke=METAL)
            f.rect(x + 59, 14, 10, 9, LED_RED)
            x += 82
        draw_outlets(f, (x, f.top + 1, f.inner_x1, f.bottom - 3), conns.get("iec", []), conns.get("3pin", []))
        return
    f.rect(f.inner_x0 + 6, f.top + 7, f.span - 12, f.bottom - f.top - 14, "#0f1115", rx=2)
    vents(f, f.inner_x0 + 10, f.h / 2 - 6, max(10, f.span - 30 - 30 * len(conns.get("psu", []))), 12)
    draw_inputs(f, f.inner_x1 - 4, conns.get("psu", []))


def ups(f: Face, rear: bool, conns: Connections) -> None:
    x0, x1 = f.inner_x0, f.inner_x1
    if not rear:
        panel_w = min(160, f.span * (0.4 if f.lanes > 1 else 0.9))
        panel_h = min(f.h - 16, 72)
        f.rect(x0, 8, panel_w, panel_h, "#0f1216", rx=4, stroke="#6c7686")
        lcd_w = panel_w * 0.58
        f.rect(x0 + 10, 16, lcd_w, panel_h * 0.55, "#0a2534", rx=2, stroke=LED_BLUE)
        f.rect(x0 + 16, 22, lcd_w * .6, 6, LED_BLUE, opacity=0.85)
        f.rect(x0 + 18, 16 + panel_h * 0.4, lcd_w * .55, 4, LED_GREEN)
        bx = x0 + lcd_w + 22
        for k in range(3):
            f.circle(bx, 22 + k * 16, 5, "#1f252d", METAL, 0.8)
        f.circle(bx + 18, 22, 3, LED_GREEN)
        vx = x0 + panel_w + 14
        if x1 - vx > 20:
            for row in range(int((f.h - 16) // 8)):
                for k in range(int((x1 - vx) // 14)):
                    f.rect(vx + k * 14, 10 + row * 8, 10, 4, "#0b0d10", rx=1)
        return
    inputs_x = draw_inputs(f, x1, conns.get("psu", []))
    right = inputs_x
    if inputs_x - x0 > 150:
        right = inputs_x - 34
        f.rect(right, f.bottom - 26, 30, 22, "#1f252d", rx=2, stroke=METAL)  # battery connector
        f.rect(right + 6, f.bottom - 20, 7, 10, LED_RED, opacity=0.8)
        f.rect(right + 17, f.bottom - 20, 7, 10, "#111418")
        right -= 6
    draw_outlets(f, (x0, f.top + 1, right, f.bottom - 4), conns.get("iec", []), conns.get("3pin", []))


def fan_tray(f: Face, rear: bool, conns: Connections) -> None:
    inputs_x = draw_inputs(f, f.inner_x1, conns.get("psu", [])) if rear else f.inner_x1 - 12
    span = inputs_x - f.inner_x0
    fans = fit(span, 108 if f.lanes == 3 else 70, 1, 4)
    pitch = span / fans
    r = min(17, (f.h - 10) / 2 - 3)
    for k in range(fans):
        cx = f.inner_x0 + pitch * (k + .5)
        f.rect(cx - pitch / 2 + 4, 3, pitch - 8, f.h - 6, "#0d1014", rx=3)
        fan(f, cx, f.h / 2, r)
    if not rear:
        f.rect(f.inner_x1 - 10, 8, 8, 4, LED_GREEN)


def shelf(f: Face, rear: bool, conns: Connections) -> None:
    x0 = f.x0 + 4
    if not rear:
        for k in range(int((f.h - 8) // 8)):
            f.rect(x0, 7 + k * 8, f.x1 - x0 - 4, 2, "#11141a", opacity=0.9)
            f.rect(x0, 9 + k * 8, f.x1 - x0 - 4, 1, "#3b424c", opacity=0.7)
    else:
        f.rect(x0, f.h - 14, f.x1 - x0 - 4, 6, "#3b424c", rx=1)
        for k in range(fit(f.x1 - x0, 60, 1, 8)):
            f.rect(x0 + 20 + k * 60, 10, 30, 3, "#11141a", rx=1)


def cable_management(f: Face, rear: bool, conns: Connections) -> None:
    x0, x1 = f.x0 + 6, f.x1 - 6
    mid = f.h / 2
    if not rear:
        f.rect(x0, mid - 4, x1 - x0, 8, "#050608")
        for k in range(int((x1 - x0) // 4)):
            f.rect(x0 + 2 + k * 4, mid - 4, 1.4, 8, "#2b313a")
    else:
        f.rect(x0, mid + 8, x1 - x0, 4, "#9aa3ae", rx=2)
    rings = fit(x1 - x0, 96, 1, 5)
    pitch = (x1 - x0) / rings
    for k in range(rings):
        x = x0 + pitch * (k + .5) - 10
        d = f"M{x:.1f} {f.h - 4:.1f}V{mid - 8:.1f}a10 10 0 0 1 20 0V{f.h - 4:.1f}"
        f.path(d, stroke="#9aa3ae", sw=4)
        f.path(d, stroke="#e5e7eb", sw=1.2, opacity=0.5)


def poe_injector(f: Face, rear: bool, conns: Connections) -> None:
    if rear:
        rear_panel(f, conns)
        return
    # A small mid-span unit: status LEDs, a printed logo and the pass-through
    # network ports on the front.
    x = status_leds(f, f.inner_x0, (LED_GREEN, LED_GREEN, LED_AMBER, LED_GREEN))
    f.rect(x, 8, 30, 28, "#0d1116", rx=3)
    glyph(f, "route", x + 15, 22, LED_GREEN)
    x += 36
    draw_data_ports(f, (x, f.top, f.inner_x1 - 6, f.bottom), conns.get("data", []))


# ------------------------------------------------- appliances and devices
def rear_panel(f: Face, conns: Connections, box=None) -> None:
    """A generic back panel: network ports left, power inputs right, vents between."""
    x0, y0, x1, y1 = box or (f.inner_x0, f.top, f.inner_x1, f.bottom)
    psus, ports = conns.get("psu", []), conns.get("data", [])
    right = x1
    if len(psus) > 1:
        psu_w = min((x1 - x0) * 0.5, psu_width(len(psus), y1 - y0))
        draw_psus(f, (x1 - psu_w, y0, x1, y1), psus)
        right = x1 - psu_w - 8
    elif psus:
        right = draw_inputs(f, x1, psus, y0 + (y1 - y0 - 22) / 2, y0 + (y1 - y0 + 22) / 2)
    left = x0
    if ports:
        port_w = min(right - x0, max(span_width(len(ports), 13, 0, 2), 13))
        draw_data_ports(f, (x0, y0, x0 + port_w, min(y1, y0 + 34)), ports, max_rows=2)
        left = x0 + port_w + 8
    if right - left > 24:
        vents(f, left, (y0 + y1) / 2 - 6, right - left - 8, 12)


def status_leds(f: Face, x, colours=(LED_GREEN, LED_GREEN, LED_AMBER, LED_GREEN)) -> float:
    """A column of status LEDs at the left of a front panel; returns the next free x."""
    f.rect(x, 8, 16, 28, "#0d1116", rx=2)
    for k, colour in enumerate(colours):
        f.rect(x + 4, 12 + k * 6, 8, 3, colour)
    return x + 22


def glyph(f: Face, kind: str, cx, cy, colour=LED_BLUE) -> None:
    """A small printed logo identifying an appliance's role."""
    if kind == "shield":
        f.path(f"M{cx:.1f} {cy - 9:.1f}l8 3v5c0 5-4 8-8 10c-4-2-8-5-8-10v-5z", stroke=colour, sw=1.6)
    elif kind == "lock":
        f.rect(cx - 6, cy - 2, 12, 10, colour, rx=1.5)
        f.path(f"M{cx - 4:.1f} {cy - 2:.1f}v-3a4 4 0 0 1 8 0v3", stroke=colour, sw=1.6)
    elif kind == "wifi":
        for r in (4, 8, 12):
            f.path(f"M{cx - r * .7:.1f} {cy + 4 - r * .7:.1f}a{r} {r} 0 0 1 {r * 1.4:.1f} 0", stroke=colour, sw=1.6)
        f.circle(cx, cy + 5, 1.6, colour)
    elif kind == "handset":
        f.path(f"M{cx - 9:.1f} {cy - 2:.1f}q0-5 9-5t9 5l-3 3l-3-2v-2h-6v2l-3 2z", fill=colour)
        f.rect(cx - 6, cy + 2, 12, 6, "none", rx=1, stroke=colour, sw=1.2)
    elif kind == "balance":
        f.path(f"M{cx - 9:.1f} {cy:.1f}h18M{cx + 5:.1f} {cy - 4:.1f}l4 4l-4 4"
               f"M{cx - 9:.1f} {cy - 6:.1f}h6l6 12h6M{cx - 9:.1f} {cy + 6:.1f}h6l6-12h6", stroke=colour, sw=1.4)
    elif kind == "route":
        f.circle(cx, cy, 9, "none", colour, 1.4)
        f.path(f"M{cx - 5:.1f} {cy:.1f}h10M{cx:.1f} {cy - 5:.1f}v10M{cx + 2:.1f} {cy - 2:.1f}l3 2l-3 2"
               f"M{cx - 2:.1f} {cy - 2:.1f}l-3 2l3 2", stroke=colour, sw=1.4)
    elif kind == "globe":
        f.circle(cx, cy, 9, "none", colour, 1.4)
        f.path(f"M{cx - 9:.1f} {cy:.1f}h18M{cx:.1f} {cy - 9:.1f}a5 9 0 0 1 0 18a5 9 0 0 1 0-18", stroke=colour, sw=1.2)
    elif kind == "door":
        f.rect(cx - 6, cy - 9, 12, 18, "none", rx=1, stroke=colour, sw=1.4)
        f.circle(cx + 3, cy, 1.4, colour)
    elif kind == "camera":
        f.rect(cx - 9, cy - 5, 13, 10, "none", rx=2, stroke=colour, sw=1.4)
        f.path(f"M{cx + 4:.1f} {cy - 2:.1f}l6-3v10l-6-3z", fill=colour)
    elif kind == "cloud":
        f.path(f"M{cx - 10:.1f} {cy + 6:.1f}a5 5 0 0 1 1-10a7 7 0 0 1 13-2a5 5 0 0 1 6 7a3 3 0 0 1-1 5z",
               stroke=colour, sw=1.6)
    else:  # chip
        f.rect(cx - 7, cy - 7, 14, 14, "none", rx=2, stroke=colour, sw=1.4)
        f.path(f"M{cx - 10:.1f} {cy - 3:.1f}h3M{cx - 10:.1f} {cy + 3:.1f}h3M{cx + 7:.1f} {cy - 3:.1f}h3"
               f"M{cx + 7:.1f} {cy + 3:.1f}h3", stroke=colour, sw=1.2)


def appliance(logo: str, accent: str = LED_BLUE, lcd: bool = False):
    """Build a drawer for a 1U network appliance: LEDs, logo, optional LCD and front ports."""
    def draw(f: Face, rear: bool, conns: Connections) -> None:
        if rear:
            rear_panel(f, conns)
            return
        x = status_leds(f, f.inner_x0)
        if f.lanes > 1 or not conns.get("data"):
            f.rect(x, 8, 28, 28, "#0d1116", rx=3)
            glyph(f, logo, x + 14, 22, accent)
            x += 34
        if lcd and f.lanes > 1:
            f.rect(x, 12, 52, 20, "#0a2534", rx=2, stroke=accent, sw=0.8)
            f.rect(x + 5, 17, 30, 3, accent, opacity=0.85)
            f.rect(x + 5, 24, 20, 3, LED_GREEN, opacity=0.8)
            x += 58
        console_x = f.inner_x1 - 12
        f.rect(console_x, 15, 10, 14, "#050608", rx=1, stroke=accent)
        draw_data_ports(f, (x, f.top, console_x - 6, f.bottom), conns.get("data", []))
    return draw


def hypervisor(f: Face, rear: bool, conns: Connections) -> None:
    server(f, rear, conns)
    if not rear:
        f.rect(f.inner_x1 - 30, f.h - 18, 26, 10, "#0d1116", rx=2)
        f.rect(f.inner_x1 - 27, f.h - 15, 20, 4, LED_BLUE, opacity=0.7)


def virtual_machine(f: Face, rear: bool, conns: Connections) -> None:
    if rear:
        rear_panel(f, conns)
        return
    # A virtual machine has no hardware: draw its placeholder as dashed guests.
    guests = fit(f.span, 70, 1, 5)
    pitch = f.span / guests
    for k in range(guests):
        x = f.inner_x0 + k * pitch + 3
        f.parts.append(f'<rect x="{x:.1f}" y="8" width="{pitch - 6:.1f}" height="28" rx="3" fill="#10151d" '
                       f'stroke="{LED_BLUE}" stroke-width="1" stroke-dasharray="4 3"/>')
        f.rect(x + 6, 14, min(24, pitch - 18), 4, LED_BLUE, opacity=0.7)
        f.rect(x + 6, 22, min(16, pitch - 18), 3, "#4c5563")
        f.rect(x + pitch - 16, 26, 4, 4, LED_GREEN)


def workstation(f: Face, rear: bool, conns: Connections) -> None:
    if rear:
        rear_panel(f, conns)
        return
    x0 = f.inner_x0
    f.rect(x0, 8, min(110, f.span * .45), 12, "#0d1116", rx=2, stroke="#6c7686")  # optical drive
    f.rect(x0 + 6, 13, min(60, f.span * .25), 2, "#3a4150")
    for k in range(2):
        f.rect(x0 + k * 11, 28, 8, 5, "#0d1a2c", stroke="#6c7686")  # USB
    f.circle(x0 + 30, 31, 3, "#1b2027", "#6c7686")  # audio
    vx = x0 + min(120, f.span * .5)
    for row in range(int((f.h - 16) // 7)):
        for k in range(int((f.inner_x1 - 36 - vx) // 9)):
            f.circle(vx + 4 + k * 9, 11 + row * 7, 2, "#0b0d10")
    f.circle(f.inner_x1 - 16, 22, 7, "#1b2027", METAL)
    f.circle(f.inner_x1 - 16, 22, 3.5, "none", LED_BLUE, 1.4)


def display(f: Face, rear: bool, conns: Connections) -> None:
    if rear:
        f.rect(f.inner_x0 + f.span / 2 - 30, 8, 60, f.h - 16, "#15191f", rx=3, stroke="#4c5563")
        for dx in (-18, 18):
            for dy in (-14, 14):
                f.circle(f.inner_x0 + f.span / 2 + dx, f.h / 2 + dy, 2.5, "#0b0d10", "#6c7686")
        rear_panel(f, conns, (f.inner_x1 - 40, f.top, f.inner_x1, f.bottom))
        return
    f.rect(f.inner_x0, 5, f.span, f.h - 10, "#07090c", rx=3, stroke="#4c5563")
    f.rect(f.inner_x0 + 6, 10, f.span - 12, f.h - 24, "#0e2233")
    f.path(f"M{f.inner_x0 + 10:.1f} {f.h - 18:.1f}L{f.inner_x0 + f.span * .45:.1f} 12", stroke="#ffffff", sw=6, opacity=0.04)
    f.rect(f.inner_x0 + f.span / 2 - 4, f.h - 11, 8, 2, LED_BLUE)


def conference(f: Face, rear: bool, conns: Connections) -> None:
    if rear:
        rear_panel(f, conns)
        return
    f.rect(f.inner_x0, 8, f.span, 28, "#101318", rx=4)
    glyph(f, "camera", f.inner_x0 + f.span / 2, 22, "#94a3b8")
    f.rect(f.inner_x1 - 14, 20, 6, 4, LED_GREEN)


def nvr(f: Face, rear: bool, conns: Connections) -> None:
    if rear:
        rear_panel(f, conns)
        return
    panel = 58 if f.lanes > 1 else 0
    rows = max(1, round(f.h / UNIT))
    bays = fit(f.span - panel - 4, 34, 1, 8)
    for row in range(rows):
        for i in range(bays):
            drive_bay(f, f.inner_x0 + i * 34, 6 + row * UNIT, 31, 32)
    if panel:
        px = f.inner_x1 - panel
        f.rect(px, 8, panel, f.h - 16, "#10151e", rx=3, stroke="#6c7686")
        glyph(f, "camera", px + panel / 2, 24, LED_RED)
        f.rect(px + 8, f.h - 22, panel - 16, 6, "#0e2a3a")
        f.rect(px + 10, f.h - 20, 10, 2, LED_GREEN)


def access_control(f: Face, rear: bool, conns: Connections) -> None:
    if rear:
        rear_panel(f, conns)
        return
    x = status_leds(f, f.inner_x0, (LED_GREEN, LED_RED, LED_AMBER, LED_OFF))
    glyph(f, "door", x + 12, 22, LED_AMBER)
    x += 30
    blocks = fit(f.inner_x1 - x, 44, 1, 8)
    for k in range(blocks):
        bx = x + k * 44
        f.rect(bx, 10, 40, 14, "#15803d", rx=1)
        for t in range(5):
            f.circle(bx + 5 + t * 7.5, 17, 2.4, "#d1d5db", "#4b5563", 0.6)
        f.rect(bx + 16, 28, 8, 4, LED_GREEN if k % 2 == 0 else LED_OFF)


def cloud_service(f: Face, rear: bool, conns: Connections) -> None:
    # Cloud services have no hardware; the panel is a labelled placeholder.
    if rear:
        return
    cx = f.inner_x0 + f.span / 2
    glyph(f, "cloud", cx, f.h / 2 - 1, "#94a3b8")
    f.rect(cx - 30, f.h - 10, 60, 2, "#3b424c", rx=1)


# Devices that are not rack-mounted sit on a shelf; the drawing is the shelf
# with the device on it, seen from the front (or its back panel from the rear).
def _shelf_base(f: Face) -> float:
    f.rect(f.x0 + 2, f.h - 7, f.x1 - f.x0 - 4, 5, "#6c7686", rx=1)
    f.rect(f.x0 + 2, f.h - 7, f.x1 - f.x0 - 4, 1.5, "#9aa3ae")
    return f.h - 7


def on_shelf(silhouette, share: float = 0.6):
    """Build a drawer for a desktop device resting on a shelf."""
    def draw(f: Face, rear: bool, conns: Connections) -> None:
        floor = _shelf_base(f)
        width = max(40.0, f.span * (share if f.lanes > 1 else min(0.95, share + 0.3)))
        x0 = f.inner_x0 + (f.span - width) / 2
        if rear:
            height = min(floor - 6, max(20.0, (floor - 6) * 0.8))
            f.rect(x0, floor - height, width, height, "#1c2129", rx=3, stroke="#4c5563")
            rear_panel(f, conns, (x0 + 5, floor - height + 3, x0 + width - 5, floor - 3))
            return
        silhouette(f, x0, width, floor)
    return draw


def _laptop(f, x, w, floor):
    f.rect(x, floor - 9, w, 9, "#9aa3ae", rx=2)
    f.rect(x, floor - 9, w, 3, "#c4cad3", rx=1.5)
    f.rect(x + w / 2 - 10, floor - 4, 20, 2, "#6c7686", rx=1)
    f.rect(x + w - 10, floor - 5, 4, 2, LED_GREEN)


def _thin_client(f, x, w, floor):
    bw = min(28, w)
    for k in range(max(1, int(w // (bw + 10)))):
        bx = x + k * (bw + 10)
        h = min(floor - 6, 34)
        f.rect(bx, floor - h, bw, h, "#1f252d", rx=3, stroke="#4c5563")
        f.circle(bx + bw / 2, floor - h + 7, 3, "none", LED_BLUE, 1.2)
        f.rect(bx + 6, floor - 12, 6, 4, "#0d1a2c", stroke="#6c7686", sw=0.6)
        f.rect(bx + 15, floor - 12, 6, 4, "#0d1a2c", stroke="#6c7686", sw=0.6)


def _tablet(f, x, w, floor):
    h = min(floor - 6, 32)
    tw = min(w, h * 1.5)
    tx = x + (w - tw) / 2
    f.path(f"M{tx + tw * .3:.1f} {floor:.1f}l6-8h{tw * .4 - 12:.1f}l6 8z", fill="#4c5563")
    f.rect(tx, floor - h - 2, tw, h - 4, "#0b0d10", rx=4, stroke="#6c7686")
    f.rect(tx + 4, floor - h + 2, tw - 8, h - 12, "#16324a")


def _mobile_phone(f, x, w, floor):
    f.rect(x, floor - 6, w, 6, "#1f252d", rx=2)  # charging dock
    h = min(floor - 8, 30)
    for k in range(max(1, min(6, int(w // 22)))):
        px = x + 4 + k * 22
        f.rect(px, floor - 4 - h, 14, h, "#0b0d10", rx=3, stroke="#6c7686", sw=0.8)
        f.rect(px + 2, floor - 1 - h, 10, h - 8, "#16324a", rx=1)
        f.rect(px + 5, floor - 6, 4, 2, LED_GREEN)


def _printer(f, x, w, floor):
    top = 6
    f.rect(x, top + 8, w, floor - top - 8, "#d7dbe0", rx=4, stroke="#9aa3ae")
    f.rect(x + 4, top, w - 8, 12, "#c4cad3", rx=3)  # scanner lid
    f.rect(x + 10, top + 22, w * .55, 5, "#2b313a", rx=1)  # output slot
    f.rect(x + w - 44, top + 18, 36, 16, "#1f252d", rx=2)  # control panel
    f.rect(x + w - 41, top + 21, 22, 10, "#16324a")
    f.circle(x + w - 13, top + 26, 2.5, LED_GREEN)
    drawer_top = top + 40
    drawers = max(1, int((floor - 4 - drawer_top) // 16))
    for k in range(drawers):
        y = drawer_top + k * 16
        f.rect(x + 6, y, w - 12, 13, "#e5e7eb", rx=2, stroke="#9aa3ae")
        f.rect(x + w / 2 - 12, y + 4, 24, 4, "#9aa3ae", rx=2)


def _scanner(f, x, w, floor):
    h = min(floor - 6, 40)
    f.rect(x, floor - h * .55, w, h * .55, "#d7dbe0", rx=3, stroke="#9aa3ae")
    f.path(f"M{x + w * .15:.1f} {floor - h * .55:.1f}l{w * .1:.1f}-{h * .4:.1f}h{w * .5:.1f}l{w * .1:.1f} {h * .4:.1f}z",
           fill="#c4cad3", stroke="#9aa3ae")  # document feeder
    f.rect(x + 8, floor - h * .3, w * .5, 3, "#2b313a")
    f.circle(x + w - 10, floor - h * .3, 2.5, LED_GREEN)


def _ip_phone(f, x, w, floor):
    pw = min(w, 90)
    for k in range(max(1, int(w // (pw + 8)))):
        px = x + k * (pw + 8)
        f.path(f"M{px:.1f} {floor:.1f}l{pw * .12:.1f}-{min(floor - 8, 40):.1f}h{pw * .76:.1f}l{pw * .12:.1f} {min(floor - 8, 40):.1f}z",
               fill="#1f252d", stroke="#4c5563")
        top = floor - min(floor - 8, 40)
        f.rect(px + pw * .18, top + 6, pw * .24, (floor - top) - 14, "#2b313a", rx=5)  # handset
        f.rect(px + pw * .48, top + 6, pw * .34, 12, "#16324a", rx=1)  # screen
        for r in range(2):
            for c in range(3):
                f.rect(px + pw * .48 + c * pw * .12, top + 22 + r * 7, pw * .09, 4, "#4c5563", rx=1)


def _camera(f, x, w, floor):
    cx = x + w / 2
    r = min(20, (floor - 8) / 2, w / 2)
    f.rect(cx - r - 6, floor - 6, 2 * r + 12, 6, "#e5e7eb", rx=2)
    f.path(f"M{cx - r:.1f} {floor - 6:.1f}a{r:.1f} {r:.1f} 0 0 1 {2 * r:.1f} 0z", fill="#1c2129", stroke="#9aa3ae")
    f.circle(cx, floor - 6 - r * .45, r * .32, "#0b0d10", "#6c7686")
    f.circle(cx, floor - 6 - r * .45, r * .12, LED_BLUE)


def _access_point(f, x, w, floor):
    cx, rw = x + w / 2, min(w / 2, 50)
    f.parts.append(f'<ellipse cx="{cx:.1f}" cy="{floor - 8:.1f}" rx="{rw:.1f}" ry="7" fill="#eef1f4" stroke="#9aa3ae"/>')
    f.parts.append(f'<ellipse cx="{cx:.1f}" cy="{floor - 10:.1f}" rx="{rw * .3:.1f}" ry="2" fill="none" stroke="{LED_BLUE}" stroke-width="1.4"/>')


def _wireless_bridge(f, x, w, floor):
    cx = x + w / 2
    f.rect(cx - 3, floor - 12, 6, 12, "#6c7686")
    f.rect(cx - 24, floor - 30, 48, 20, "#eef1f4", rx=4, stroke="#9aa3ae")
    f.rect(cx + 14, floor - 25, 4, 2, LED_GREEN)
    glyph(f, "wifi", cx - 6, floor - 22, "#9aa3ae")


def _iot(f, x, w, floor):
    bw = min(w, 60)
    bx = x + (w - bw) / 2
    f.rect(bx, floor - 18, bw, 18, "#eef1f4", rx=6, stroke="#9aa3ae")
    for k, colour in enumerate((LED_GREEN, LED_BLUE, LED_OFF)):
        f.circle(bx + 10 + k * 8, floor - 9, 2, colour)


STYLES = {
    "server": ("#4a5361", "#262c35", "#5b6574", "#2b323c"),
    "switch": ("#2f5a49", "#17332a", "#6fb296", "#243a31"),
    "storage": ("#3b4a63", "#1d2533", "#7c90b3", "#2a3446"),
    "patch_panel": ("#2c3138", "#15181d", METAL, "#22262c"),
    "kvm": ("#3a414b", "#1b1f25", "#5b6574", "#2b323c"),
    "pdu": ("#2a2d33", "#121418", METAL, "#1f2226"),
    "ups": ("#2e333b", "#15181d", "#5b6574", "#23272d"),
    "fan_tray": ("#353b44", "#1a1e24", "#5b6574", "#2b323c"),
    "shelf": ("#2a2f36", "#191c21", "#5b6574", "#22262c"),
    "cable_management": ("#23272d", "#121418", "#5b6574", "#1c1f24"),
    "poe_injector": ("#33414f", "#18222b", "#5f7d94", "#233139"),
}
_APPLIANCE = ("#343b45", "#1b2027", "#5b6574", "#262c35")
_SHELF = ("#15181d", "#0e1013", "#3b424c", "#22262c")
STYLES.update({
    "modem": _APPLIANCE, "router": ("#2f4a5a", "#18282f", "#6f9bb2", "#233640"),
    "firewall": ("#4a2c30", "#281618", "#b26f76", "#3a2226"), "vpn_gateway": ("#3a3150", "#1e1a2b", "#8f7cb3", "#2c2640"),
    "load_balancer": ("#2f5a57", "#17332f", "#6fb2ac", "#243a38"), "wireless_controller": ("#2d3f5f", "#172033", "#6f8fc2", "#232f45"),
    "hypervisor": STYLES["server"], "virtual_machine": ("#1d232c", "#12161c", "#3b82f6", "#1a1f27"),
    "workstation": _APPLIANCE, "display": ("#1a1d22", "#0d0f12", "#4c5563", "#15181c"),
    "conference": _APPLIANCE, "nvr": ("#3a3339", "#1d191d", "#8a7c8a", "#2b262b"),
    "access_control": ("#3a3a33", "#1d1d19", "#8a8a7c", "#2b2b26"), "phone_system": ("#3b3a4d", "#1e1d27", "#8584a8", "#2b2a38"),
    "cloud_service": ("#23272d", "#15181d", "#4c5563", "#1c1f24"), "other": _APPLIANCE,
    **{key: _SHELF for key in ("laptop", "thin_client", "tablet", "mobile_phone", "printer", "scanner",
                               "ip_phone", "camera", "access_point", "wireless_bridge", "iot")},
})
DRAWERS = {
    "server": server, "switch": switch, "storage": storage, "patch_panel": patch_panel,
    "kvm": kvm, "pdu": pdu, "ups": ups, "fan_tray": fan_tray, "shelf": shelf,
    "cable_management": cable_management, "poe_injector": poe_injector,
    "modem": appliance("globe", LED_GREEN), "router": appliance("route"),
    "firewall": appliance("shield", LED_RED, lcd=True), "vpn_gateway": appliance("lock", "#a78bfa"),
    "load_balancer": appliance("balance", LED_GREEN, lcd=True), "wireless_controller": appliance("wifi"),
    "phone_system": appliance("handset", "#a5b4fc"), "other": appliance("chip", "#94a3b8"),
    "hypervisor": hypervisor, "virtual_machine": virtual_machine, "workstation": workstation,
    "display": display, "conference": conference, "nvr": nvr, "access_control": access_control,
    "cloud_service": cloud_service,
    "laptop": on_shelf(_laptop), "thin_client": on_shelf(_thin_client, 0.5), "tablet": on_shelf(_tablet, 0.3),
    "mobile_phone": on_shelf(_mobile_phone, 0.5), "printer": on_shelf(_printer, 0.7),
    "scanner": on_shelf(_scanner, 0.6), "ip_phone": on_shelf(_ip_phone, 0.7), "camera": on_shelf(_camera, 0.3),
    "access_point": on_shelf(_access_point, 0.5), "wireless_bridge": on_shelf(_wireless_bridge, 0.4),
    "iot": on_shelf(_iot, 0.3),
}


def side_connectors(key: str, rear: bool) -> tuple[str, ...]:
    """Connector kinds physically on one side of a type."""
    item_type = rack_item_types.get(key)
    return tuple(c for c in item_type.connector_keys if (c in item_type.rear_connectors) == rear)


def default_connections(key: str, rear: bool) -> dict[str, list[bool]]:
    counts = rack_item_types.get(key).default_counts
    return {connector: [False] * counts.get(connector, 0) for connector in side_connectors(key, rear)}


def connections_from_ports(ports: Iterable[Mapping], key: str, rear: bool) -> dict[str, list[bool]]:
    """Group an item's ports into per-connector connected flags for one side."""
    wanted = side_connectors(key, rear)
    grouped: dict[str, list[bool]] = {connector: [] for connector in wanted}
    for port in sorted(ports, key=lambda row: int(row.get("port_number") or 0)):
        connector = str(port.get("connector") or "data")
        if connector in grouped:
            grouped[connector].append(bool(port.get("asset_id") or port.get("label")
                                           or port.get("source_port_id") or port.get("peer_port_id")
                                           or port.get("peer") or port.get("fed_items")))
    return grouped


def render(key: str, width_lanes: int, units: int, rear: bool,
           connections: Connections | None = None) -> str:
    """Return an SVG faceplate for one side of a rack item."""
    catalogue = rack_item_types.get(key)
    lanes = width_lanes if width_lanes in WIDTHS else 3
    face = Face(WIDTHS[lanes], UNIT * max(1, units), lanes, *STYLES[catalogue.key])
    conns = connections if connections is not None else default_connections(catalogue.key, rear)
    DRAWERS[catalogue.key](face, rear, conns)
    return face.svg()


def data_uri(svg: str) -> str:
    # Single quotes are escaped: the URI is embedded in url('...') inside a style attribute.
    return "data:image/svg+xml," + quote(svg, safe=" =:/,;.-_()")
