"""ASSET_TYPE_MODE: auto, custom and manual asset type pickers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader
from pydantic import ValidationError

import app.main as main_module
from app.core.config import Settings
from app.features.assets import routes as assets_routes
from app.services import asset_types

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


@pytest.fixture
def mode(monkeypatch):
    def set_mode(value: str, existing: list[str] | None = None):
        monkeypatch.setattr(main_module.settings, "asset_type_mode", value, raising=False)
        monkeypatch.setattr(assets_routes.asset_repo, "list_custom_asset_types",
                            AsyncMock(return_value=existing or []))
    return set_mode


@pytest.mark.parametrize(("raw", "expected"), [
    ("", "auto"), ("Custom", "custom"), (" manual  # no IT", "manual"), ("auto", "auto"),
])
def test_setting_normalises_mode(raw, expected):
    assert Settings._normalize_asset_type_mode(raw) == expected


def test_setting_rejects_unknown_mode():
    with pytest.raises((ValueError, ValidationError)):
        Settings._normalize_asset_type_mode("sometimes")


@pytest.mark.asyncio
async def test_auto_mode_requires_a_catalogue_key(mode):
    mode("auto")
    values = await assets_routes._manual_asset_values({"name": "R1", "asset_type": "router"}, 1)
    assert (values["asset_type"], values["type"]) == ("router", "Router")
    with pytest.raises(HTTPException):
        await assets_routes._manual_asset_values({"name": "X", "asset_type": "forklift"}, 1)


@pytest.mark.asyncio
async def test_custom_mode_accepts_catalogue_and_typed_types(mode):
    mode("custom", ["Forklift"])
    values = await assets_routes._manual_asset_values({"name": "R1", "asset_type_name": "router"}, 1)
    assert (values["asset_type"], values["type"]) == ("router", "Router")
    values = await assets_routes._manual_asset_values({"name": "F1", "asset_type_name": "FORKLIFT"}, 1)
    assert (values["asset_type"], values["type"]) == ("custom", "Forklift")
    values = await assets_routes._manual_asset_values({"name": "P1", "asset_type_name": "Pallet jack"}, 1)
    assert (values["asset_type"], values["type"]) == ("custom", "Pallet jack")
    # Catalogue keys from API clients still work.
    values = await assets_routes._manual_asset_values({"name": "S1", "asset_type": "switch"}, 1)
    assert values["asset_type"] == "switch"


@pytest.mark.asyncio
async def test_manual_mode_stores_every_type_as_custom(mode):
    mode("manual")
    values = await assets_routes._manual_asset_values({"name": "R1", "asset_type_name": "Router"}, 1)
    assert (values["asset_type"], values["type"]) == ("custom", "Router")
    with pytest.raises(HTTPException):
        await assets_routes._manual_asset_values({"name": "R1", "asset_type": "router"}, 1)


@pytest.mark.asyncio
async def test_saving_other_fields_keeps_the_current_type(mode):
    mode("auto")
    current = {"asset_type": "custom", "type": "Forklift"}
    values = await assets_routes._manual_asset_values(
        {"name": "F1", "asset_type": "custom", "type": "Forklift"}, 1, current)
    assert (values["asset_type"], values["type"]) == ("custom", "Forklift")
    mode("manual")
    current = {"asset_type": "router", "type": "Router"}
    values = await assets_routes._manual_asset_values({"name": "R1", "asset_type": "router"}, 1, current)
    assert (values["asset_type"], values["type"]) == ("router", "Router")


def _picker(**kwargs):
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    macro = env.get_template("assets/_type_picker.html").module.asset_type_picker
    return str(macro(**kwargs))


def test_picker_markup_per_mode():
    groups = asset_types.grouped()
    auto = _picker(mode="auto", groups=groups, custom_types=[])
    assert '<select' in auto and 'value="router"' in auto and "datalist" not in auto
    custom = _picker(mode="custom", groups=groups, custom_types=["Forklift"])
    assert 'name="asset_type_name"' in custom
    assert '<option value="Router">' in custom and '<option value="Forklift">' in custom
    manual = _picker(mode="manual", groups=[], custom_types=["Forklift"])
    assert '<option value="Forklift">' in manual and "Router" not in manual
    # A custom type stays selectable after switching back to auto mode.
    kept = _picker(mode="auto", groups=groups, custom_types=[],
                   current=asset_types.CUSTOM_TYPE, current_label="Forklift")
    assert '<option value="custom" selected>Forklift</option>' in kept
