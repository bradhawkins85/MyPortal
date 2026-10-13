"""The privileged installer must remain pinned to the reviewed upstream binary."""

import hashlib
import importlib.util
from pathlib import Path

import pytest


def load_builder():
    path = Path(__file__).resolve().parents[1] / "rmm/thermal-bundle/build.py"
    spec = importlib.util.spec_from_file_location("rmm_thermal_builder", path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder


def test_pawnio_bundle_rejects_changed_installer():
    builder = load_builder()
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        builder.verify_installer(b"unverified installer")


def test_pawnio_bundle_preserves_verified_bytes(monkeypatch):
    builder = load_builder()
    installer = b"test installer bytes"
    monkeypatch.setattr(builder, "PAWNIO_INSTALLER_SHA256", hashlib.sha256(installer).hexdigest())
    assert builder.verify_installer(installer) == installer
