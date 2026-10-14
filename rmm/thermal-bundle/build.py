"""Build the pinned .NET Framework sensor bundle before compiling Windows agents.

Run from rmm/: python3 thermal-bundle/build.py. No endpoint downloads required.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "thermal-bundle" / "ThermalBundle.csproj"
DEST = ROOT / "internal" / "thermal" / "bundle"
LHM_REVISION = "3d331e3370efb858411f19511373eff65a218701"
PAWNIO_INSTALLER_SHA256 = "a3a46226c5e2824f4cdd42be0eecbabfc672c86f7889710f5ab1e6ad385b47a0"


def download_bytes(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def download(url):
    return download_bytes(url).decode("utf-8-sig")


def verify_installer(data):
    if hashlib.sha256(data).hexdigest() != PAWNIO_INSTALLER_SHA256:
        raise RuntimeError("PawnIO 2.1.0 installer checksum mismatch")
    return data


def bundle_pawnio():
    origin = f"https://raw.githubusercontent.com/LibreHardwareMonitor/LibreHardwareMonitor/{LHM_REVISION}/"
    installer = verify_installer(download_bytes(origin + "LibreHardwareMonitor/Resources/PawnIO_setup.exe"))
    (DEST / "PawnIO_setup.exe").write_bytes(installer)
    # Accompany the unmodified GPL driver and LGPL modules with corresponding
    # source, including the driver source's pinned PawnPP submodule.
    for repo, revision in (
        ("PawnIO", "e1043d7750f501b6b0d3ac0a71bd5b9406cd17b6"),
        ("PawnPP", "e64e4c37b2d8ba0d8ee57205faf8183aee12c438"),
        ("PawnIO.Modules", "4aa792beb020a14c8261072e9786d2dfb38489d9"),
    ):
        source = download_bytes(f"https://codeload.github.com/namazso/{repo}/tar.gz/{revision}")
        (DEST / f"{repo}-source.tar.gz").write_bytes(source)
    (DEST / "PawnIO-COPYING.txt").write_text(download(
        "https://raw.githubusercontent.com/namazso/PawnIO/e1043d7750f501b6b0d3ac0a71bd5b9406cd17b6/COPYING"
    ), encoding="utf-8")


def build():
    subprocess.run(["dotnet", "restore", str(PROJECT), "--locked-mode"], check=True)
    arch = os.environ.get("THERMAL_ARCH", "amd64")
    runtime = {"amd64": "win-x64", "arm64": "win-arm64"}[arch]
    subprocess.run(["dotnet", "build", str(PROJECT), "--no-restore", "-c", "Release", "--runtime", runtime], check=True)
    output = PROJECT.parent / "bin" / "Release" / "net472" / runtime
    for old in DEST.glob("*.dll"):
        old.unlink()
    for dll in output.glob("*.dll"):
        if dll.name != "ThermalBundle.dll":
            shutil.copy2(dll, DEST / dll.name)
    if not (DEST / "LibreHardwareMonitorLib.dll").is_file():
        raise RuntimeError("LibreHardwareMonitor DLL missing from bundle")
    assets = json.loads((PROJECT.parent / "obj" / "project.assets.json").read_text())
    package_root = Path(next(iter(assets["packageFolders"])))
    notices = []
    for name, info in sorted(assets["libraries"].items()):
        if info["type"] != "package" or name.startswith("Microsoft.NETFramework.ReferenceAssemblies"):
            continue
        folder = package_root / info["path"]
        metadata = ET.parse(next(folder.glob("*.nuspec"))).getroot()
        license_node = metadata.find(".//{*}license")
        if license_node is None:
            raise RuntimeError(f"Missing license metadata for {name}")
        license_name = license_node.text.strip()
        if license_node.get("type") == "file":
            license_text = (folder / license_name).read_text(encoding="utf-8-sig")
        elif license_node.get("type") == "expression" and license_name in {"MIT", "MPL-2.0", "BSD-3-Clause"}:
            license_text = download(f"https://raw.githubusercontent.com/spdx/license-list-data/v3.26.0/text/{license_name}.txt")
        else:
            raise RuntimeError(f"Unsupported license {license_name} for {name}; preserve its notices before shipping")
        copyright_node = metadata.find(".//{*}copyright")
        repository = metadata.find(".//{*}repository")
        origin = "" if repository is None else f"Source: {repository.get('url', '')} @ {repository.get('commit', '')}"
        copyright_text = "" if copyright_node is None else copyright_node.text
        notices.append(f"{name}\n{copyright_text}\n{origin}\n{license_name}\n{license_text}\n")
        for notice in folder.glob("*NOTICE*"):
            notices.append(notice.read_text(encoding="utf-8-sig"))
    (DEST / "third-party-notices.txt").write_text("\n".join(notices), encoding="utf-8")
    (DEST / "upstream-notices.txt").write_text(download(
        "https://raw.githubusercontent.com/LibreHardwareMonitor/LibreHardwareMonitor/"
        "3d331e3370efb858411f19511373eff65a218701/THIRD-PARTY-NOTICES.txt"), encoding="utf-8")
    (DEST / "LICENSE.txt").write_text(download(
        "https://raw.githubusercontent.com/LibreHardwareMonitor/LibreHardwareMonitor/"
        "3d331e3370efb858411f19511373eff65a218701/LICENSE"), encoding="utf-8")
    bundle_pawnio()


if __name__ == "__main__":
    build()
