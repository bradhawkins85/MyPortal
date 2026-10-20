"""Build Microsoft Intune Win32 app packages (``.intunewin``) on the server.

An ``.intunewin`` file is what Microsoft's IntuneWinAppUtil.exe produces and
what Intune accepts as a "Windows app (Win32)" upload.  It is a zip holding:

* ``IntuneWinPackage/Contents/IntunePackage.intunewin`` - a zip of the setup
  folder, encrypted with AES-256-CBC and authenticated with HMAC-SHA256.  The
  file is ``HMAC (32 bytes) | IV (16 bytes) | ciphertext`` and the HMAC covers
  the IV and ciphertext.
* ``IntuneWinPackage/Metadata/Detection.xml`` - the keys, a SHA-256 of the
  unencrypted zip and, for an MSI setup file, the MSI's product details so
  Intune can fill in the install command and detection rule.

Building it here means the Windows build agent only has to produce the MSI.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import struct
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# The IntuneWinAppUtil.exe version whose output this matches.
TOOL_VERSION = "1.8.6.0"

_CHUNK = 1024 * 1024


class MsiReadError(ValueError):
    """The file is not an MSI database this module can read."""


@dataclass(frozen=True)
class MsiInfo:
    product_code: str
    product_version: str
    package_code: str
    upgrade_code: str
    publisher: str
    machine_install: bool
    includes_services: bool


# ---------------------------------------------------------------------------
# MSI reading
#
# An MSI is an OLE compound file.  Tables are streams with encoded names whose
# columns are stored one after another, and string cells are indexes into the
# shared string pool.  Only the Property table and the summary information
# stream are needed here.
# ---------------------------------------------------------------------------

_CFB_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
_FREE_SECT = 0xFFFFFFFF
_END_OF_CHAIN = 0xFFFFFFFE
_NO_STREAM = 0xFFFFFFFF
_MSI_NAME_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._"


class _CompoundFile:
    def __init__(self, handle) -> None:
        self._handle = handle
        header = self._read_at(0, 512)
        if len(header) < 512 or header[:8] != _CFB_SIGNATURE:
            raise MsiReadError("Not an MSI file.")
        self._sector_size = 1 << struct.unpack_from("<H", header, 0x1E)[0]
        self._mini_sector_size = 1 << struct.unpack_from("<H", header, 0x20)[0]
        if self._sector_size not in (512, 4096):
            raise MsiReadError("Unsupported MSI sector size.")
        first_dir = struct.unpack_from("<I", header, 0x30)[0]
        self._mini_cutoff = struct.unpack_from("<I", header, 0x38)[0]
        first_minifat = struct.unpack_from("<I", header, 0x3C)[0]
        first_difat, difat_count = struct.unpack_from("<II", header, 0x44)

        fat_sectors = [s for s in struct.unpack_from("<109I", header, 0x4C) if s != _FREE_SECT]
        per_sector = self._sector_size // 4
        sector = first_difat
        for _ in range(difat_count):
            if sector in (_END_OF_CHAIN, _FREE_SECT):
                break
            entries = struct.unpack(f"<{per_sector}I", self._sector(sector))
            fat_sectors.extend(s for s in entries[:-1] if s != _FREE_SECT)
            sector = entries[-1]
        self._fat: list[int] = []
        for fat_sector in fat_sectors:
            self._fat.extend(struct.unpack(f"<{per_sector}I", self._sector(fat_sector)))

        directory = b"".join(self._sector(s) for s in self._chain(first_dir))
        self._entries = [directory[i : i + 128] for i in range(0, len(directory) - 127, 128)]
        if not self._entries:
            raise MsiReadError("The MSI has no directory.")
        root = self._entries[0]
        self._mini_stream = self._read_chain(
            struct.unpack_from("<I", root, 116)[0], struct.unpack_from("<Q", root, 120)[0]
        )
        minifat = self._read_chain(first_minifat, None) if first_minifat != _END_OF_CHAIN else b""
        self._minifat = list(struct.unpack(f"<{len(minifat) // 4}I", minifat[: len(minifat) // 4 * 4]))
        self.streams = self._root_streams(struct.unpack_from("<I", root, 76)[0])

    def _read_at(self, offset: int, size: int) -> bytes:
        self._handle.seek(offset)
        return self._handle.read(size)

    def _sector(self, sector: int) -> bytes:
        data = self._read_at((sector + 1) * self._sector_size, self._sector_size)
        if len(data) != self._sector_size:
            raise MsiReadError("The MSI file is truncated.")
        return data

    def _chain(self, start: int, table: list[int] | None = None) -> list[int]:
        table = self._fat if table is None else table
        chain: list[int] = []
        sector = start
        while sector not in (_END_OF_CHAIN, _FREE_SECT):
            if sector >= len(table) or len(chain) > len(table):
                raise MsiReadError("The MSI sector chain is damaged.")
            chain.append(sector)
            sector = table[sector]
        return chain

    def _read_chain(self, start: int, size: int | None) -> bytes:
        data = b"".join(self._sector(s) for s in self._chain(start))
        return data if size is None else data[:size]

    def _root_streams(self, child: int) -> dict[str, tuple[int, int]]:
        streams: dict[str, tuple[int, int]] = {}
        pending = [child]
        seen: set[int] = set()
        while pending:
            index = pending.pop()
            if index == _NO_STREAM or index in seen or index >= len(self._entries):
                continue
            seen.add(index)
            entry = self._entries[index]
            name_len = struct.unpack_from("<H", entry, 64)[0]
            name = entry[: max(0, name_len - 2)].decode("utf-16-le", errors="replace")
            left, right = struct.unpack_from("<II", entry, 68)
            pending.extend((left, right))
            if entry[66] == 2:
                start = struct.unpack_from("<I", entry, 116)[0]
                size = struct.unpack_from("<Q", entry, 120)[0]
                if self._sector_size == 512:
                    size &= 0xFFFFFFFF
                streams[_decode_stream_name(name)] = (start, size)
        return streams

    def read(self, name: str) -> bytes | None:
        location = self.streams.get(name)
        if location is None:
            return None
        start, size = location
        if size < self._mini_cutoff:
            chunks = [
                self._mini_stream[s * self._mini_sector_size : (s + 1) * self._mini_sector_size]
                for s in self._chain(start, self._minifat)
            ]
            return b"".join(chunks)[:size]
        return self._read_chain(start, size)


def _decode_stream_name(name: str) -> str:
    out: list[str] = []
    for char in name:
        code = ord(char)
        if 0x3800 <= code < 0x4800:
            code -= 0x3800
            out.append(_MSI_NAME_CHARS[code & 0x3F])
            out.append(_MSI_NAME_CHARS[(code >> 6) & 0x3F])
        elif 0x4800 <= code < 0x4840:
            out.append(_MSI_NAME_CHARS[code - 0x4800])
        elif code == 0x4840:
            out.append("!")
        else:
            out.append(char)
    return "".join(out)


def _string_pool(cfb: _CompoundFile) -> tuple[list[str], int]:
    pool = cfb.read("!_StringPool")
    data = cfb.read("!_StringData")
    if pool is None or data is None or len(pool) < 4:
        raise MsiReadError("The MSI has no string table.")
    words = struct.unpack(f"<{len(pool) // 2}H", pool[: len(pool) // 2 * 2])
    codepage = words[0] | ((words[1] & 0x7FFF) << 16)
    ref_size = 3 if words[1] & 0x8000 else 2
    encoding = "utf-8" if codepage == 65001 else f"cp{codepage}" if codepage else "cp1252"
    try:
        "".encode(encoding)
    except LookupError:
        encoding = "cp1252"
    strings = [""]
    offset = 0
    i = 2
    while i + 1 < len(words):
        length, refs = words[i], words[i + 1]
        if length == 0 and refs == 0:
            strings.append("")
            i += 2
            continue
        if length == 0 and i + 3 < len(words):
            # A string over 64 KB takes two entries: an empty length, then
            # its length split across the second entry's two words.
            length = (words[i + 3] << 16) | words[i + 2]
            i += 4
        else:
            i += 2
        strings.append(data[offset : offset + length].decode(encoding, errors="replace"))
        offset += length
    return strings, ref_size


def _string_table(cfb: _CompoundFile, name: str, columns: int) -> list[tuple[str, ...]]:
    strings, ref_size = _string_pool(cfb)
    raw = cfb.read("!" + name)
    if raw is None:
        return []
    rows = len(raw) // (ref_size * columns)

    def cell(column: int, row: int) -> str:
        offset = (column * rows + row) * ref_size
        index = int.from_bytes(raw[offset : offset + ref_size], "little")
        return strings[index] if index < len(strings) else ""

    return [tuple(cell(c, r) for c in range(columns)) for r in range(rows)]


def _summary_revision(cfb: _CompoundFile) -> str:
    """Return the summary information revision number (the package code)."""

    stream = cfb.read("\x05SummaryInformation")
    if not stream or len(stream) < 48:
        return ""
    section = struct.unpack_from("<I", stream, 44)[0]
    if section + 8 > len(stream):
        return ""
    count = struct.unpack_from("<I", stream, section + 4)[0]
    for i in range(min(count, 64)):
        pid, offset = struct.unpack_from("<II", stream, section + 8 + i * 8)
        start = section + offset
        if pid != 9 or start + 8 > len(stream):
            continue
        value_type, length = struct.unpack_from("<II", stream, start)
        if value_type != 30:  # VT_LPSTR
            return ""
        return stream[start + 8 : start + 8 + length].split(b"\0", 1)[0].decode("latin-1")
    return ""


def read_msi_info(path: Path | str) -> MsiInfo:
    """Read the product details Intune shows for an MSI setup file."""

    with open(path, "rb") as handle:
        cfb = _CompoundFile(handle)
        properties = dict(row for row in _string_table(cfb, "Property", 2) if len(row) == 2)
        package_code = _summary_revision(cfb)
        includes_services = "!ServiceInstall" in cfb.streams
    product_code = properties.get("ProductCode", "")
    if not re.fullmatch(r"\{[0-9A-Fa-f-]{36}\}", product_code):
        raise MsiReadError("The MSI has no product code.")
    return MsiInfo(
        product_code=product_code,
        product_version=properties.get("ProductVersion", ""),
        package_code=package_code,
        upgrade_code=properties.get("UpgradeCode", ""),
        publisher=properties.get("Manufacturer", ""),
        machine_install=properties.get("ALLUSERS") == "1",
        includes_services=includes_services,
    )


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _detection_xml(
    *,
    setup_name: str,
    content_size: int,
    encryption_key: bytes,
    mac_key: bytes,
    iv: bytes,
    mac: bytes,
    digest: bytes,
    msi: MsiInfo | None,
) -> str:
    lines = [
        f'<ApplicationInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" ToolVersion="{TOOL_VERSION}">',
        f"  <Name>{escape(setup_name)}</Name>",
        f"  <UnencryptedContentSize>{content_size}</UnencryptedContentSize>",
        "  <FileName>IntunePackage.intunewin</FileName>",
        f"  <SetupFile>{escape(setup_name)}</SetupFile>",
        "  <EncryptionInfo>",
        f"    <EncryptionKey>{_b64(encryption_key)}</EncryptionKey>",
        f"    <MacKey>{_b64(mac_key)}</MacKey>",
        f"    <InitializationVector>{_b64(iv)}</InitializationVector>",
        f"    <Mac>{_b64(mac)}</Mac>",
        "    <ProfileIdentifier>ProfileVersion1</ProfileIdentifier>",
        f"    <FileDigest>{_b64(digest)}</FileDigest>",
        "    <FileDigestAlgorithm>SHA256</FileDigestAlgorithm>",
        "  </EncryptionInfo>",
    ]
    if msi is not None:
        lines += [
            "  <MsiInfo>",
            f"    <MsiProductCode>{escape(msi.product_code)}</MsiProductCode>",
            f"    <MsiProductVersion>{escape(msi.product_version)}</MsiProductVersion>",
            f"    <MsiPackageCode>{escape(msi.package_code)}</MsiPackageCode>",
            f"    <MsiPublisher>{escape(msi.publisher)}</MsiPublisher>",
            f"    <MsiUpgradeCode>{escape(msi.upgrade_code)}</MsiUpgradeCode>",
            f"    <MsiExecutionContext>{'System' if msi.machine_install else 'Any'}</MsiExecutionContext>",
            "    <MsiRequiresLogon>false</MsiRequiresLogon>",
            "    <MsiRequiresReboot>false</MsiRequiresReboot>",
            f"    <MsiIsMachineInstall>{_bool(msi.machine_install)}</MsiIsMachineInstall>",
            f"    <MsiIsUserInstall>{_bool(not msi.machine_install)}</MsiIsUserInstall>",
            f"    <MsiIncludesServices>{_bool(msi.includes_services)}</MsiIncludesServices>",
            "    <MsiIncludesODBCDataSource>false</MsiIncludesODBCDataSource>",
            f"    <MsiContainsSystemRegistryKeys>{_bool(msi.machine_install)}</MsiContainsSystemRegistryKeys>",
            "    <MsiContainsSystemFolders>false</MsiContainsSystemFolders>",
            "  </MsiInfo>",
        ]
    lines.append("</ApplicationInfo>")
    return "\n".join(lines) + "\n"


def build_package(setup_file: Path | str, output: Path | str, *, setup_name: str | None = None) -> None:
    """Package ``setup_file`` as an ``.intunewin`` file at ``output``.

    ``setup_name`` is the file name Intune shows and installs from; it
    defaults to ``setup_file``'s own name.  MSI details are read when the
    setup file is an MSI.
    """

    setup_file = Path(setup_file)
    setup_name = setup_name or setup_file.name
    msi = read_msi_info(setup_file) if setup_name.lower().endswith(".msi") else None
    encryption_key, mac_key, iv = os.urandom(32), os.urandom(32), os.urandom(16)

    with tempfile.TemporaryDirectory(prefix="intunewin-", dir=Path(output).parent) as work:
        content_zip = Path(work) / "content.zip"
        with zipfile.ZipFile(content_zip, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.write(setup_file, setup_name)

        digest = hashlib.sha256()
        content_size = 0
        encrypted = Path(work) / "IntunePackage.intunewin"
        encryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(iv)).encryptor()
        padder = padding.PKCS7(128).padder()
        mac = hmac.new(mac_key, iv, hashlib.sha256)
        with content_zip.open("rb") as source, encrypted.open("wb") as target:
            target.write(b"\0" * 32 + iv)
            for chunk in iter(lambda: source.read(_CHUNK), b""):
                digest.update(chunk)
                content_size += len(chunk)
                block = encryptor.update(padder.update(chunk))
                mac.update(block)
                target.write(block)
            block = encryptor.update(padder.finalize()) + encryptor.finalize()
            mac.update(block)
            target.write(block)
            mac_value = mac.digest()
            target.seek(0)
            target.write(mac_value)
        content_zip.unlink()

        detection = _detection_xml(
            setup_name=setup_name,
            content_size=content_size,
            encryption_key=encryption_key,
            mac_key=mac_key,
            iv=iv,
            mac=mac_value,
            digest=digest.digest(),
            msi=msi,
        )
        # The encrypted content does not compress, so it is stored as is.
        with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED, allowZip64=True) as package:
            package.write(encrypted, "IntuneWinPackage/Contents/IntunePackage.intunewin")
            package.writestr(
                "IntuneWinPackage/Metadata/Detection.xml",
                detection.encode("utf-8"),
                compress_type=zipfile.ZIP_DEFLATED,
            )
