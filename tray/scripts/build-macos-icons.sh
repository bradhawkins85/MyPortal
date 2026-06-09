#!/usr/bin/env bash
# Generate the macOS .icns asset for the MyPortal tray/chat-shell bundle.
#
# The repository intentionally stores only this text build recipe; PNG/iconset
# intermediates and the final .icns are generated during macOS builds so binary
# icon files do not need to be committed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRAY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_OUTPUT="${TRAY_DIR}/chat-shell/build/icon.icns"
OUTPUT_PATH="${1:-${DEFAULT_OUTPUT}}"
ICONSET_DIR="${OUTPUT_PATH%.icns}.iconset"

case "${OUTPUT_PATH}" in
  *.icns) ;;
  *)
    echo "Output path must end in .icns: ${OUTPUT_PATH}" >&2
    exit 2
    ;;
esac

mkdir -p "$(dirname "${OUTPUT_PATH}")"
rm -rf "${ICONSET_DIR}"
mkdir -p "${ICONSET_DIR}"

python3 - "${ICONSET_DIR}" <<'PY'
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

out = Path(sys.argv[1])

# Standard macOS iconset members.  The @2x files intentionally duplicate the
# pixel size of the next logical point size, matching Apple's iconutil input
# contract (for example icon_16x16@2x.png is a 32x32 PNG).
outputs = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}

BG = (0x0F, 0x17, 0x2A, 0xFF)
RING_START = (0x38, 0xBD, 0xF8, 0xFF)
RING_END = (0x63, 0x66, 0xF1, 0xFF)
INNER = BG

def chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

def blend(a: tuple[int, int, int, int], b: tuple[int, int, int, int], t: float) -> tuple[int, int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(4))

def png_rgba(size: int) -> bytes:
    cx = cy = (size - 1) / 2.0
    corner_radius = size * 0.25
    ring_radius = size * 0.34375
    inner_radius = size * 0.1875
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            # Rounded-square signed distance field with light edge smoothing.
            px = abs((x + 0.5) - size / 2) - (size / 2 - corner_radius)
            py = abs((y + 0.5) - size / 2) - (size / 2 - corner_radius)
            ox = max(px, 0.0)
            oy = max(py, 0.0)
            outside = (ox * ox + oy * oy) ** 0.5 + min(max(px, py), 0.0) - corner_radius
            alpha = 255 if outside <= -1 else 0 if outside >= 1 else round((1 - outside) * 127.5)
            if alpha <= 0:
                rows.extend((0, 0, 0, 0))
                continue

            dx = x - cx
            dy = y - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist <= inner_radius:
                colour = INNER
            elif dist <= ring_radius:
                t = min(1.0, max(0.0, (x + y) / (2 * (size - 1))))
                colour = blend(RING_START, RING_END, t)
            else:
                colour = BG
            rows.extend((colour[0], colour[1], colour[2], min(alpha, colour[3])))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")

for name, size in outputs.items():
    (out / name).write_bytes(png_rgba(size))
PY

if ! command -v iconutil >/dev/null 2>&1; then
  echo "iconutil is required to create ${OUTPUT_PATH}. Run this target on macOS with Xcode Command Line Tools installed." >&2
  echo "Generated iconset remains at ${ICONSET_DIR} for inspection." >&2
  exit 1
fi

iconutil -c icns "${ICONSET_DIR}" -o "${OUTPUT_PATH}"
rm -rf "${ICONSET_DIR}"
echo "Built macOS icon: ${OUTPUT_PATH}"
