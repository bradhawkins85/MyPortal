"""Generate the rack faceplate SVGs in app/static/images/racks/.

Every rack item type gets a front and rear image at each lane width
(1/3, 2/3 and full). Full-width items are drawn with rack ears; narrower
items are drawn as standalone shelf-mounted units with fewer ports and bays,
so they are never a cropped copy of the full-width faceplate.

Run from the repository root:  python scripts/generate_rack_faceplates.py
"""
from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app" / "static" / "images" / "racks"
UNIT = 44  # 1U height in viewBox units
WIDTHS = {1: 160, 2: 320, 3: 480}
IMAGE_UNITS = {"storage": 2, "ups": 2}

LED_GREEN, LED_BLUE, LED_AMBER, LED_RED = "#34d399", "#38bdf8", "#fbbf24", "#d03b3b"
METAL = "#8a94a3"


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
                for unit in range(height // UNIT):
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

    @property
    def span(self) -> float:
        return self.inner_x1 - self.inner_x0

    def rect(self, x, y, w, h, fill, rx=0, stroke=None, sw=0.8, opacity=None):
        attrs = f'x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" fill="{fill}"'
        if rx:
            attrs += f' rx="{rx:g}"'
        if stroke:
            attrs += f' stroke="{stroke}" stroke-width="{sw:g}"'
        if opacity is not None:
            attrs += f' opacity="{opacity:g}"'
        self.parts.append(f"<rect {attrs}/>")

    def circle(self, cx, cy, r, fill="none", stroke=None, sw=1.0):
        extra = f' stroke="{stroke}" stroke-width="{sw:g}"' if stroke else ""
        self.parts.append(f'<circle cx="{cx:g}" cy="{cy:g}" r="{r:g}" fill="{fill}"{extra}/>')

    def path(self, d, fill="none", stroke=None, sw=1.0, opacity=None):
        extra = f' stroke="{stroke}" stroke-width="{sw:g}"' if stroke else ""
        if opacity is not None:
            extra += f' opacity="{opacity:g}"'
        self.parts.append(f'<path d="{d}" fill="{fill}"{extra}/>')

    def svg(self) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" preserveAspectRatio="none">'
                f"<defs>{self.defs}</defs>{''.join(self.parts)}</svg>\n")


def fit(span: float, pitch: float, minimum: int = 1, maximum: int = 999) -> int:
    return max(minimum, min(maximum, int(span // pitch)))


# ---------------------------------------------------------------- components
def rj45(f: Face, x, y, w=11, h=9, frame="#8a94a3"):
    f.rect(x, y, w, h, "#050608", rx=1, stroke=frame)
    f.rect(x + w / 2 - 2.5, y + h - 3, 5, 3, frame, opacity=0.6)


def iec_outlet(f: Face, x, y, s=1.0):
    """C13/C19 style outlet: a notched trapezoid with three pins."""
    w, h = 26 * s, 20 * s
    f.path(f"M{x} {y}h{w}v{h * .72}l-{4 * s} {h * .28}h-{w - 8 * s}l-{4 * s}-{h * .28}z",
           fill="#050608", stroke="#a4acb8")
    for dx in (6, 11.5, 17):
        f.rect(x + dx * s, y + 6 * s, 2.6 * s, 7 * s, "#a4acb8")


def three_pin_outlet(f: Face, x, y, s=1.0):
    """AU/NZ style 3-pin socket: angled active/neutral and a vertical earth."""
    w = 26 * s
    f.rect(x, y, w, 24 * s, "#e5e7eb", rx=3 * s, stroke="#9aa3ae")
    f.path(f"M{x + 7 * s} {y + 7 * s}l{3 * s} {5 * s}", stroke="#111418", sw=2.2 * s)
    f.path(f"M{x + 19 * s} {y + 7 * s}l-{3 * s} {5 * s}", stroke="#111418", sw=2.2 * s)
    f.path(f"M{x + 13 * s} {y + 14 * s}v{6 * s}", stroke="#111418", sw=2.2 * s)
    f.circle(x + 22 * s, y + 20 * s, 1.4 * s, "#d03b3b")


def iec_inlet(f: Face, x, y, s=1.0):
    f.rect(x, y, 22 * s, 16 * s, "#050608", rx=2 * s, stroke="#a4acb8")
    for dx in (5, 10, 15):
        f.rect(x + dx * s, y + 5 * s, 2 * s, 6 * s, "#a4acb8")


def psu(f: Face, x, y, w, h):
    f.rect(x, y, w, h, "#1b2027", rx=2, stroke="#6c7686")
    iec_inlet(f, x + 5, y + (h - 16) / 2)
    grille_x = x + 32
    for k in range(max(1, int((w - 38) // 5))):
        f.rect(grille_x + k * 5, y + 5, 3, h - 10, "#0b0e12", rx=1)
    f.rect(x + w - 8, y + 5, 4, 4, LED_GREEN)


def fan(f: Face, cx, cy, r):
    f.circle(cx, cy, r, "#07090b", "#6c7686")
    for ring in (r * .35, r * .65):
        f.circle(cx, cy, ring, "none", "#4c5563", 0.8)
    f.path(f"M{cx - r} {cy}h{2 * r}M{cx} {cy - r}v{2 * r}", stroke="#4c5563", sw=0.8)
    f.circle(cx, cy, r * .18, "#6c7686")


def vents(f: Face, x, y, w, h, pitch=5):
    for k in range(max(1, int(w // pitch))):
        f.rect(x + k * pitch, y, pitch - 2, h, "#101419", rx=1)


def drive_bay(f: Face, x, y, w, h, blue=True):
    f.rect(x, y, w, h, "#15191f", rx=2, stroke="#6c7686")
    f.rect(x + 3, y + 4, w - 6, h * .55, "#232932")
    for k in range(max(1, int((w - 8) // 4))):
        f.rect(x + 4 + k * 4, y + 6, 2, h * .45, "#11151a")
    f.rect(x + 4, y + h - 7, 4, 3, LED_GREEN)
    if blue:
        f.rect(x + 10, y + h - 7, 4, 3, LED_BLUE, opacity=0.8)


# -------------------------------------------------------------------- types
def server(width: int, lanes: int, rear: bool) -> Face:
    f = Face(width, UNIT, lanes, "#4a5361", "#262c35")
    x0, span = f.inner_x0, f.span
    if not rear:
        bays = fit(span * (0.6 if lanes > 1 else 0.7), 34, 1, 8)
        for i in range(bays):
            drive_bay(f, x0 + i * 34, 6, 31, 32)
        rest = x0 + bays * 34 + 4
        vents(f, rest, 9, max(5, f.inner_x1 - rest - 36), 26)
        f.rect(f.inner_x1 - 30, 12, 26, 20, "#1b2027", rx=3, stroke="#6c7686")
        f.circle(f.inner_x1 - 17, 22, 5, "none", LED_BLUE, 1.6)
    else:
        psus = 2 if lanes == 3 else 1
        psu_w = 70 if lanes > 1 else 60
        for k in range(psus):
            psu(f, f.inner_x1 - (k + 1) * (psu_w + 4), 5, psu_w, 34)
        io_x1 = f.inner_x1 - psus * (psu_w + 4) - 6
        nics = 4 if lanes == 3 else (2 if lanes == 2 else 1)
        for k in range(nics):
            rj45(f, x0 + k * 14, 8)
            f.rect(x0 + k * 14 + 1, 6, 3, 1.6, LED_GREEN)
        x = x0 + nics * 14 + 6
        if lanes > 1:
            f.rect(x, 8, 7, 9, "#0d1a2c", stroke="#6c7686")  # USB
            f.rect(x + 10, 8, 7, 9, "#0d1a2c", stroke="#6c7686")
            f.path(f"M{x + 22} 8h22l-3 9h-16z", fill="#0b3b8c", stroke="#6c7686")  # VGA
            x += 50
        slots = fit(io_x1 - x, 30, 0, 4)
        for k in range(slots):
            f.rect(x + k * 30, 22, 26, 16, "#1b2027", rx=1, stroke="#4c5563")
            vents(f, x + k * 30 + 3, 25, 20, 10, 4)
        if lanes == 1:
            rj45(f, x0, 24)
    return f


def switch(width: int, lanes: int, rear: bool) -> Face:
    f = Face(width, UNIT, lanes, "#2f5a49", "#17332a", "#6fb296", "#243a31")
    x0 = f.inner_x0
    if not rear:
        f.rect(x0, 8, 16, 28, "#10261e", rx=2)
        for k, colour in enumerate((LED_GREEN, LED_GREEN, LED_AMBER, LED_GREEN)):
            f.rect(x0 + 4, 12 + k * 6, 8, 3, colour)
        sfp = 4 if lanes == 3 else (2 if lanes == 2 else 0)
        sfp_x = f.inner_x1 - sfp * 18 - (14 if lanes > 1 else 0)
        cols = fit(sfp_x - x0 - 24, 13, 2, 24)
        for col in range(cols):
            for row in range(2):
                gx = x0 + 22 + col * 13 + (col // 6) * 3
                rj45(f, gx, 7 + row * 16)
                f.rect(gx + 1, 5 + row * 16, 3, 1.6, LED_GREEN, opacity=0.9)
        for k in range(sfp):
            f.rect(sfp_x + k * 18, 11, 15, 22, "#0b0f12", rx=1, stroke="#b0bac6", sw=1)
        if lanes > 1:
            f.rect(f.inner_x1 - 10, 15, 10, 14, "#050608", rx=1, stroke=LED_BLUE)
    else:
        psus = 2 if lanes > 1 else 1
        for k in range(psus):
            psu(f, f.inner_x1 - (k + 1) * 74, 5, 70, 34)
        fans_x1 = f.inner_x1 - psus * 74 - 6
        fans = fit(fans_x1 - x0 - 30, 40, 0, 4)
        for k in range(fans):
            fan(f, x0 + 30 + k * 40 + 17, 22, 15)
        rj45(f, x0, 9, frame=LED_BLUE)  # console
        rj45(f, x0, 25)  # management
    return f


def storage(width: int, lanes: int, rear: bool) -> Face:
    f = Face(width, UNIT * 2, lanes, "#3b4a63", "#1d2533", "#7c90b3", "#2a3446")
    x0 = f.inner_x0
    if not rear:
        panel = 36 if lanes > 1 else 0
        cols = fit(f.span - panel - 4, 98 if lanes > 1 else 70, 1, 4)
        sled_w = (f.span - panel - 4) / cols - 4
        for row in range(3):
            for col in range(cols):
                x, y = x0 + col * (sled_w + 4), 6 + row * 26
                f.rect(x, y, sled_w, 23, "#141a24", rx=2, stroke="#7c90b3")
                for k in range(max(1, int((sled_w - 30) // 6))):
                    f.rect(x + 6 + k * 6, y + 5, 3, 13, "#0a0e14")
                f.rect(x + sled_w - 8, y + 5, 4, 4, LED_GREEN)
                f.rect(x + sled_w - 8, y + 13, 4, 4, LED_BLUE)
        if panel:
            px = f.inner_x1 - panel
            f.rect(px, 6, panel, 76, "#10151e", rx=3, stroke="#7c90b3")
            f.rect(px + 6, 14, panel - 12, 10, "#0e2a3a")
            f.rect(px + 8, 17, panel - 20, 3, LED_BLUE)
            for k in range(4):
                f.rect(px + 8, 34 + k * 10, panel - 16, 5, "#253047", rx=2)
    else:
        controllers = 2 if lanes > 1 else 1
        psu_w = 70 if lanes > 1 else 60
        ctrl_x1 = f.inner_x1 - psu_w - 6
        ctrl_w = (ctrl_x1 - x0) / controllers - 6
        for c in range(controllers):
            cx = x0 + c * (ctrl_w + 6)
            f.rect(cx, 6, ctrl_w, 34, "#141a24", rx=2, stroke="#7c90b3")
            ports = fit(ctrl_w - 12, 14, 1, 6)
            for k in range(ports):
                rj45(f, cx + 6 + k * 14, 12)
            for k in range(fit(ctrl_w - 12, 22, 1, 3)):
                f.rect(cx + 6 + k * 22, 26, 18, 9, "#0b0f12", rx=1, stroke="#b0bac6")  # SAS
            f.rect(cx, 46, ctrl_w, 36, "#141a24", rx=2, stroke="#7c90b3")
            fan(f, cx + ctrl_w / 2, 64, 14)
        psu(f, f.inner_x1 - psu_w, 6, psu_w, 34)
        psu(f, f.inner_x1 - psu_w, 48, psu_w, 34)
    return f


def patch_panel(width: int, lanes: int, rear: bool) -> Face:
    f = Face(width, UNIT, lanes, "#2c3138", "#15181d", METAL, "#22262c")
    x0 = f.inner_x0
    groups = fit(f.span, 106, 1, 4)
    per_group = 6 if lanes > 1 else fit(f.span - 8, 16, 2, 6)
    group_w = per_group * 16 + 2
    gap = (f.span - groups * group_w) / max(groups, 1)
    for g in range(groups):
        gx = x0 + gap / 2 + g * (group_w + gap)
        if not rear:
            f.rect(gx - 4, 4, group_w + 4, 36, "#0f1215", rx=2)
            for col in range(per_group):
                x = gx + col * 16
                f.rect(x, 8, 12, 5, "#e5e7eb", opacity=0.85)
                rj45(f, x, 17, 12, 16, "#a4acb8")
        else:
            f.rect(gx - 4, 6, group_w + 4, 22, "#0f1215", rx=2)
            for col in range(per_group):
                x = gx + col * 16
                f.rect(x, 9, 12, 16, "#1f252d", rx=1, stroke="#6c7686")  # keystone back
                f.rect(x + 3, 12, 6, 10, "#2f6fd6", opacity=0.8)  # cable jacket
            f.rect(gx - 4, 32, group_w + 4, 4, "#9aa3ae", rx=2)  # support bar
    return f


def kvm(width: int, lanes: int, rear: bool) -> Face:
    f = Face(width, UNIT, lanes, "#3a414b", "#1b1f25")
    x0, x1 = f.inner_x0, f.inner_x1
    if not rear:
        f.rect(x0, 5, f.span, 12, "#0d1116", rx=2, stroke="#6c7686", sw=0.6)
        f.rect(x0 + f.span * .3, 8, f.span * .38, 6, "#0e2a3a")
        f.rect(x0 + f.span * .31, 9.5, f.span * .12, 3, LED_BLUE, opacity=0.8)
        handle = 26 if lanes > 1 else 16
        keys = fit(f.span - 2 * handle - 20, 14, 4, 26)
        start = x0 + handle + 8 + ((f.span - 2 * handle - 16) - keys * 14) / 2
        for row in range(2):
            for k in range(keys):
                f.rect(start + k * 14, 21 + row * 7, 11, 5, "#1f252d", rx=1, stroke="#4c5563", sw=0.5)
        f.rect(x0, 20, handle, 20, "#252b33", rx=3, stroke=METAL, sw=1)
        f.rect(x1 - handle, 20, handle, 20, "#252b33", rx=3, stroke=METAL, sw=1)
    else:
        channels = fit(f.span - 40, 40, 1, 8)
        for k in range(channels):
            cx = x0 + k * 40
            f.path(f"M{cx} 9h24l-3 9h-18z", fill="#0b3b8c", stroke="#6c7686")  # VGA
            f.rect(cx + 2, 24, 8, 9, "#0d1a2c", stroke="#6c7686")
            f.rect(cx + 14, 24, 8, 9, "#0d1a2c", stroke="#6c7686")
        iec_inlet(f, x1 - 24, 14)
    return f


def pdu(width: int, lanes: int, rear: bool) -> Face:
    f = Face(width, UNIT, lanes, "#2a2d33", "#121418", METAL, "#1f2226")
    x0, x1 = f.inner_x0, f.inner_x1
    if not rear:
        meter = lanes > 1
        x = x0
        if meter:
            f.rect(x, 8, 46, 28, "#0b0d10", rx=2, stroke="#6c7686")
            f.rect(x + 5, 14, 36, 16, "#061a12")
            for k in range(3):
                f.rect(x + 9 + k * 10, 21, 7, 2, LED_GREEN, opacity=0.8)
            f.rect(x + 54, 10, 20, 24, "#1f2226", rx=2, stroke=METAL)
            f.rect(x + 59, 14, 10, 9, LED_RED)
            x += 82
        outlets = fit(x1 - x, 34, 1, 10)
        pitch = (x1 - x) / outlets
        for k in range(outlets):
            ox = x + k * pitch + (pitch - 26) / 2
            iec_outlet(f, ox, 10)
            f.rect(ox + 10, 35, 6, 2, LED_GREEN)
    else:
        f.rect(x0 + 6, 12, f.span - 12, 20, "#0f1115", rx=2)
        f.circle(x1 - 18, 22, 9, "#050608", "#a4acb8", 1.4)  # cord gland
        f.path(f"M{x1 - 18} 31v13", stroke="#050608", sw=7)  # input cord
        vents(f, x0 + 10, 16, max(10, f.span - 50), 12)
    return f


def ups(width: int, lanes: int, rear: bool) -> Face:
    f = Face(width, UNIT * 2, lanes, "#2e333b", "#15181d", "#5b6574", "#23272d")
    x0, x1 = f.inner_x0, f.inner_x1
    if not rear:
        panel_w = min(160, f.span * (0.4 if lanes > 1 else 0.9))
        f.rect(x0, 8, panel_w, 72, "#0f1216", rx=4, stroke="#6c7686")
        lcd_w = panel_w * 0.58
        f.rect(x0 + 10, 16, lcd_w, 40, "#0a2534", rx=2, stroke=LED_BLUE)
        f.rect(x0 + 16, 22, lcd_w * .6, 6, LED_BLUE, opacity=0.85)
        f.rect(x0 + 16, 33, lcd_w * .45, 4, LED_BLUE, opacity=0.55)
        f.rect(x0 + 16, 42, lcd_w * .8, 8, "none", stroke=LED_BLUE, sw=1)
        f.rect(x0 + 18, 44, lcd_w * .55, 4, LED_GREEN)
        bx = x0 + lcd_w + 22
        for k in range(3):
            f.circle(bx, 22 + k * 16, 5, "#1f252d", METAL, 0.8)
        f.circle(bx + 18, 22, 3, LED_GREEN)
        f.circle(bx + 18, 38, 3, "#3a3320")
        f.circle(bx + 18, 54, 3, "#3a1f1f")
        f.rect(x0 + 10, 64, panel_w - 20, 8, "#1f252d", rx=2)
        vx = x0 + panel_w + 14
        if x1 - vx > 20:
            for row in range(9):
                for k in range(int((x1 - vx) // 14)):
                    f.rect(vx + k * 14, 10 + row * 8, 10, 4, "#0b0d10", rx=1)
    else:
        # Outlet banks: IEC on the top row, 3-pin mains on the bottom row.
        iec = fit(f.span * 0.7, 32, 1, 8)
        for k in range(iec):
            iec_outlet(f, x0 + k * 32, 8)
        three = fit(f.span * 0.7, 32, 1, 6 if lanes == 3 else (3 if lanes == 2 else 2))
        for k in range(three):
            three_pin_outlet(f, x0 + k * 32, 36)
        right = x0 + max(iec, three) * 32 + 8
        if x1 - right > 30:
            iec_inlet(f, right, 10, 1.3)  # C20 input
            f.rect(right, 40, 30, 22, "#1f252d", rx=2, stroke=METAL)  # battery connector
            f.rect(right + 6, 46, 7, 10, LED_RED, opacity=0.8)
            f.rect(right + 17, 46, 7, 10, "#111418")
            if x1 - right > 130:
                fan(f, right + 62, 44, 18)
                f.rect(right + 90, 10, 34, 24, "#141a24", rx=2, stroke="#6c7686")  # network card
                rj45(f, right + 100, 17)
        f.rect(x0, 66, min(f.span, 200), 14, "#141a24", rx=2)
        f.rect(x0 + 4, 70, min(f.span, 200) - 8, 6, "#1f252d", rx=1)
    return f


def fan_tray(width: int, lanes: int, rear: bool) -> Face:
    f = Face(width, UNIT, lanes, "#353b44", "#1a1e24")
    fans = fit(f.span, 108 if lanes == 3 else 70, 1, 4)
    pitch = f.span / fans
    for k in range(fans):
        cx = f.inner_x0 + pitch * (k + .5)
        f.rect(cx - pitch / 2 + 4, 3, pitch - 8, 38, "#0d1014", rx=3)
        fan(f, cx, 22, 17)
    if not rear:
        f.rect(f.inner_x1 - 10, 8, 8, 4, LED_GREEN)
    else:
        iec_inlet(f, f.inner_x1 - 24, 14) if lanes == 3 else None
    return f


def shelf(width: int, lanes: int, rear: bool) -> Face:
    f = Face(width, UNIT, lanes, "#2a2f36", "#191c21", "#5b6574", "#22262c")
    x0 = f.x0 + 4
    if not rear:
        for k in range(4):
            f.rect(x0, 7 + k * 8, f.x1 - x0 - 4, 2, "#11141a", opacity=0.9)
            f.rect(x0, 9 + k * 8, f.x1 - x0 - 4, 1, "#3b424c", opacity=0.7)
    else:
        f.rect(x0, 30, f.x1 - x0 - 4, 6, "#3b424c", rx=1)  # shelf lip
        for k in range(fit(f.x1 - x0, 60, 1, 8)):
            f.rect(x0 + 20 + k * 60, 10, 30, 3, "#11141a", rx=1)  # vent slots
    return f


def cable_management(width: int, lanes: int, rear: bool) -> Face:
    f = Face(width, UNIT, lanes, "#23272d", "#121418", "#5b6574", "#1c1f24")
    x0, x1 = f.x0 + 6, f.x1 - 6
    if not rear:
        f.rect(x0, 18, x1 - x0, 8, "#050608")
        for k in range(int((x1 - x0) // 4)):
            f.rect(x0 + 2 + k * 4, 18, 1.4, 8, "#2b313a")
    else:
        f.rect(x0, 30, x1 - x0, 4, "#9aa3ae", rx=2)
    rings = fit(x1 - x0, 96, 1, 5)
    pitch = (x1 - x0) / rings
    for k in range(rings):
        x = x0 + pitch * (k + .5) - 10
        f.path(f"M{x} 40V14a10 10 0 0 1 20 0V40", stroke="#9aa3ae", sw=4)
        f.path(f"M{x} 40V14a10 10 0 0 1 20 0V40", stroke="#e5e7eb", sw=1.2, opacity=0.5)
    return f


DRAWERS = {
    "server": server, "switch": switch, "storage": storage, "patch_panel": patch_panel,
    "kvm": kvm, "pdu": pdu, "ups": ups, "fan_tray": fan_tray, "shelf": shelf,
    "cable_management": cable_management,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*.svg"):
        stale.unlink()
    for key, draw in DRAWERS.items():
        for lanes, width in WIDTHS.items():
            for rear in (False, True):
                face = draw(width, lanes, rear)
                name = f"{key}-w{lanes}{'-rear' if rear else ''}.svg"
                (OUT / name).write_text(face.svg(), encoding="utf-8")
    print(f"Wrote {len(DRAWERS) * len(WIDTHS) * 2} faceplates to {OUT}")


if __name__ == "__main__":
    main()
