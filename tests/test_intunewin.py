from __future__ import annotations

import base64
import hashlib
import hmac
import io
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.repositories import tray as tray_repo
from app.services import intunewin
from app.services import tray_deployment_builds as builds

PRODUCT_CODE = "{6D39BF88-E543-4153-8258-05D4F33DCF20}"
PACKAGE_CODE = "{72F44231-A8C1-4356-9015-57AC0641515D}"
UPGRADE_CODE = "{A3E7F4C2-8B1D-4F5E-9A2C-1D6E3B7F8A4C}"
_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _table_name(name: str) -> str:
    out = [chr(0x4840)]
    for i in range(0, len(name), 2):
        pair = name[i : i + 2]
        if len(pair) == 2:
            out.append(chr(0x3800 + _CHARS.index(pair[0]) + (_CHARS.index(pair[1]) << 6)))
        else:
            out.append(chr(0x4800 + _CHARS.index(pair)))
    return "".join(out)


def _compound_file(streams: list[tuple[str, bytes]]) -> bytes:
    """Write a version 3 compound file with every stream in regular sectors.

    The mini stream cutoff is 0 so no stream needs the mini stream.
    """

    sector = 512
    data_sectors: list[bytes] = []
    entries: list[tuple[str, int, int, int]] = []
    for name, data in streams:
        start = len(data_sectors)
        padded = data + b"\0" * (-len(data) % sector)
        data_sectors.extend(padded[i : i + sector] for i in range(0, len(padded), sector))
        entries.append((name, start, len(data), len(padded) // sector))

    directory_count = -(-(len(entries) + 1) // 4)
    # Sector 0 is the FAT, then the directory, then stream data.
    dir_first = 1
    data_first = dir_first + directory_count
    fat = [0xFFFFFFFF] * (sector // 4)
    fat[0] = 0xFFFFFFFD
    for i in range(directory_count):
        fat[dir_first + i] = dir_first + i + 1 if i + 1 < directory_count else 0xFFFFFFFE
    for _name, start, _size, count in entries:
        for i in range(count):
            index = data_first + start + i
            fat[index] = index + 1 if i + 1 < count else 0xFFFFFFFE

    def entry(name: str, kind: int, right: int, child: int, start: int, size: int) -> bytes:
        encoded = (name + "\0").encode("utf-16-le")
        raw = bytearray(128)
        raw[: len(encoded)] = encoded
        struct.pack_into("<HBB", raw, 64, len(encoded), kind, 1)
        struct.pack_into("<III", raw, 68, 0xFFFFFFFF, right, child)
        struct.pack_into("<IQ", raw, 116, start, size)
        return bytes(raw)

    directory = [entry("Root Entry", 5, 0xFFFFFFFF, 1 if entries else 0xFFFFFFFF, 0xFFFFFFFE, 0)]
    for i, (name, start, size, _count) in enumerate(entries):
        right = i + 2 if i + 1 < len(entries) else 0xFFFFFFFF
        directory.append(entry(name, 2, right, 0xFFFFFFFF, data_first + start, size))
    directory_bytes = b"".join(directory)
    directory_bytes += b"\0" * (directory_count * sector - len(directory_bytes))

    header = bytearray(512)
    header[:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<HHHHH", header, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 0x28, 0, 1, dir_first, 0, 0, 0xFFFFFFFE, 0, 0xFFFFFFFE, 0)
    difat = [0] + [0xFFFFFFFF] * 108
    struct.pack_into("<109I", header, 0x4C, *difat)
    return bytes(header) + struct.pack(f"<{sector // 4}I", *fat) + directory_bytes + b"".join(data_sectors)


def _summary_information(revision: str) -> bytes:
    value = revision.encode("latin-1") + b"\0"
    prop = struct.pack("<II", 30, len(value)) + value
    section = struct.pack("<II", 16 + len(prop), 1) + struct.pack("<II", 9, 16) + prop
    header = struct.pack("<HHI16sI", 0xFFFE, 0, 0, b"\0" * 16, 1)
    header += bytes(16) + struct.pack("<I", 48)
    return header + section


def _msi(properties: dict[str, str], *, services: bool = True) -> bytes:
    strings: list[str] = []
    for key, value in properties.items():
        for text in (key, value):
            if text not in strings:
                strings.append(text)
    pool = struct.pack("<HH", 1252, 0) + b"".join(
        struct.pack("<HH", len(s.encode("cp1252")), 1) for s in strings
    )
    string_data = b"".join(s.encode("cp1252") for s in strings)
    keys = [strings.index(k) + 1 for k in properties]
    values = [strings.index(v) + 1 for v in properties.values()]
    table = struct.pack(f"<{len(keys) * 2}H", *keys, *values)
    streams = [
        (_table_name("_StringPool"), pool),
        (_table_name("_StringData"), string_data),
        (_table_name("Property"), table),
        ("\x05SummaryInformation", _summary_information(PACKAGE_CODE)),
    ]
    if services:
        streams.append((_table_name("ServiceInstall"), b"\0" * 32))
    return _compound_file(streams)


TRAY_PROPERTIES = {
    "ProductCode": PRODUCT_CODE,
    "ProductVersion": "0.9.5",
    "UpgradeCode": UPGRADE_CODE,
    "Manufacturer": "MyPortal",
    "ALLUSERS": "1",
}


def _decrypt(package: Path) -> tuple[ET.Element, bytes]:
    with zipfile.ZipFile(package) as archive:
        assert sorted(archive.namelist()) == [
            "IntuneWinPackage/Contents/IntunePackage.intunewin",
            "IntuneWinPackage/Metadata/Detection.xml",
        ]
        detection = ET.fromstring(archive.read("IntuneWinPackage/Metadata/Detection.xml"))
        encrypted = archive.read("IntuneWinPackage/Contents/IntunePackage.intunewin")
    info = detection.find("EncryptionInfo")
    key = base64.b64decode(info.findtext("EncryptionKey"))
    mac_key = base64.b64decode(info.findtext("MacKey"))
    iv = base64.b64decode(info.findtext("InitializationVector"))
    mac = base64.b64decode(info.findtext("Mac"))
    assert encrypted[:32] == mac
    assert hmac.compare_digest(hmac.new(mac_key, encrypted[32:], hashlib.sha256).digest(), mac)
    assert encrypted[32:48] == iv
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    unpadder = padding.PKCS7(128).unpadder()
    content = unpadder.update(decryptor.update(encrypted[48:]) + decryptor.finalize()) + unpadder.finalize()
    assert base64.b64decode(info.findtext("FileDigest")) == hashlib.sha256(content).digest()
    assert int(detection.findtext("UnencryptedContentSize")) == len(content)
    return detection, content


def test_read_msi_info(tmp_path):
    msi = tmp_path / "tray.msi"
    msi.write_bytes(_msi(TRAY_PROPERTIES))

    info = intunewin.read_msi_info(msi)

    assert info == intunewin.MsiInfo(
        product_code=PRODUCT_CODE,
        product_version="0.9.5",
        package_code=PACKAGE_CODE,
        upgrade_code=UPGRADE_CODE,
        publisher="MyPortal",
        machine_install=True,
        includes_services=True,
    )


def test_read_msi_info_rejects_other_files(tmp_path):
    path = tmp_path / "setup.msi"
    path.write_bytes(b"MZ" + b"\0" * 1024)
    with pytest.raises(intunewin.MsiReadError):
        intunewin.read_msi_info(path)


def test_read_msi_info_requires_product_code(tmp_path):
    path = tmp_path / "setup.msi"
    path.write_bytes(_msi({"Manufacturer": "MyPortal"}))
    with pytest.raises(intunewin.MsiReadError):
        intunewin.read_msi_info(path)


def test_build_package_for_msi(tmp_path):
    msi_bytes = _msi(TRAY_PROPERTIES)
    msi = tmp_path / "myportal-tray.msi"
    msi.write_bytes(msi_bytes)
    package = tmp_path / "out.intunewin"

    intunewin.build_package(msi, package, setup_name="MyPortal-Tray-Acme-Setup.msi")

    detection, content = _decrypt(package)
    assert detection.findtext("SetupFile") == "MyPortal-Tray-Acme-Setup.msi"
    assert detection.findtext("FileName") == "IntunePackage.intunewin"
    with zipfile.ZipFile(io.BytesIO(content)) as inner:
        assert inner.namelist() == ["MyPortal-Tray-Acme-Setup.msi"]
        assert inner.read("MyPortal-Tray-Acme-Setup.msi") == msi_bytes
    msi_info = detection.find("MsiInfo")
    assert msi_info.findtext("MsiProductCode") == PRODUCT_CODE
    assert msi_info.findtext("MsiUpgradeCode") == UPGRADE_CODE
    assert msi_info.findtext("MsiPackageCode") == PACKAGE_CODE
    assert msi_info.findtext("MsiExecutionContext") == "System"
    assert msi_info.findtext("MsiIsMachineInstall") == "true"
    assert msi_info.findtext("MsiIncludesServices") == "true"


def test_build_package_for_exe_has_no_msi_info(tmp_path):
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"MZ" + bytes(range(256)) * 20)
    package = tmp_path / "out.intunewin"

    intunewin.build_package(setup, package)

    detection, _content = _decrypt(package)
    assert detection.findtext("SetupFile") == "setup.exe"
    assert detection.find("MsiInfo") is None


@pytest.mark.anyio
async def test_ready_intunewin_is_built_once_and_kept_with_the_build(monkeypatch, tmp_path):
    monkeypatch.setattr(builds, "_STORAGE_DIR", tmp_path)
    msi = builds.artifact_path(42, "msi")
    msi.parent.mkdir(parents=True)
    msi.write_bytes(_msi(TRAY_PROPERTIES))
    monkeypatch.setattr(
        tray_repo,
        "get_latest_ready_deployment_build",
        AsyncMock(return_value={"id": 42, "status": "ready"}),
    )
    calls = []
    real_build = intunewin.build_package

    def counting_build(*args, **kwargs):
        calls.append(args)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(intunewin, "build_package", counting_build)

    first = await builds.ready_intunewin(7, "Setup.msi")
    second = await builds.ready_intunewin(7, "Setup.msi")

    assert first == second == builds.intunewin_path(42)
    assert len(calls) == 1
    detection, _content = _decrypt(first)
    assert detection.findtext("SetupFile") == "Setup.msi"
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.anyio
async def test_ready_intunewin_without_build(monkeypatch):
    monkeypatch.setattr(
        tray_repo, "get_latest_ready_deployment_build", AsyncMock(return_value=None)
    )
    assert await builds.ready_intunewin(7, "Setup.msi") is None
