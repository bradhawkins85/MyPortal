from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services import cpu_catalog
from scripts.import_intel_cpus import build


def test_model_matching_preserves_suffixes_and_variants():
    cpu = cpu_catalog.lookup("Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz", today=date(2026, 11, 12))
    assert cpu["product"] == "Core i5-8250U"
    assert cpu["release_date"] == "Q3'17"
    assert cpu["approx_age"] == 9.2
    assert cpu["max_operating_temp_c"] is None
    assert cpu_catalog.lookup("Intel Core i5-8250") is None
    assert cpu_catalog.lookup("AMD Ryzen 7 5800X") is None
    assert cpu_catalog.lookup(None) is None
    assert cpu_catalog.lookup("11th Gen Intel(R) Core(TM) i7-1165G7 @ 2.80GHz")["product"] == "Core i7-1165G7"
    assert cpu_catalog.model_key("Core i9-14900KF") != cpu_catalog.model_key("Core i9-14900K")


def test_age_is_dynamic_and_explicit_age_is_preserved():
    model = "Core Ultra 7 155H"
    first = cpu_catalog.lookup(model, today=date(2024, 11, 15))
    second = cpu_catalog.lookup(model, today=date(2025, 11, 15))
    assert first["approx_age"] == 1.0
    assert second["approx_age"] == 2.0
    assert first["max_operating_temp_c"] == 110
    assert cpu_catalog.lookup(model, today=date(2023, 1, 1))["approx_age"] is None
    original = {"cpu_name": model, "approx_age": 0}
    assert cpu_catalog.enrich_asset(original)["approx_age"] == 0
    assert original == {"cpu_name": model, "approx_age": 0}
    assert cpu_catalog.enrich_asset({"cpu_name": model})["approx_age"] is not None


def test_import_uses_numeric_versions_and_keeps_legacy_models(tmp_path):
    header = "Product,Release Date,Max Operating Temp.(°C)\n"
    (tmp_path / "Intel_Core_v1_9.csv").write_text(header + "Core Test,Q1'24,100\nCore Legacy,Q2'01,N/A\n")
    (tmp_path / "Intel_Core_v1_10.csv").write_text(header + "Core Test,Q2'25,105\n")
    output = tmp_path / "result" / "cpus.csv"
    assert build(tmp_path, output) == (2, 1)
    assert "Core Test,Q2'25,105,Intel_Core_v1_10.csv" in output.read_text()
    assert "Core Legacy,Q2'01,,Intel_Core_v1_9.csv" in output.read_text()
    (tmp_path / "Intel_Core_v1_10.csv").write_text(header + "Core Test,Q2'25,nan\n")
    with pytest.raises(ValueError, match="Invalid temperature"):
        build(tmp_path, output)


@pytest.mark.asyncio
async def test_asset_repository_prefills_missing_age_without_writes(monkeypatch):
    from app.repositories import assets

    row = {"id": 1, "company_id": 2, "cpu_name": "Core i5-8250U", "approx_age": None}
    monkeypatch.setattr(assets.db, "fetch_one", AsyncMock(return_value=row))
    monkeypatch.setattr(assets.db, "fetch_all", AsyncMock(return_value=[row]))
    execute = AsyncMock()
    monkeypatch.setattr(assets.db, "execute", execute)
    assert (await assets.get_asset_by_id(1))["approx_age"] is not None
    assert (await assets.list_company_assets(2))[0]["approx_age"] is not None
    assert row["approx_age"] is None
    execute.assert_not_called()


def test_health_prompt_includes_reference_without_changing_alert_thresholds():
    from app.services import ai_health_check as health

    snapshot = {"sections": {"cpu": {"name": "Intel Core Ultra 7 155H"}}, "sensors": {
        "readings": [{"type": "Temperature", "hardware_type": "Cpu", "value": 97}]}}
    compact = health._snapshot_for_prompt(snapshot)
    assert compact["cpu_reference"]["max_operating_temp_c"] == 110
    assert "T-junction" in compact["cpu_reference"]["temperature_note"]
    assert "cpu_reference" not in snapshot
    assert any(s["key"] == "cpu_temperature_high" and s["severity"] == "critical"
               for s in health.evaluate_snapshot(snapshot))


def test_hardware_reference_renders_with_and_without_temperature():
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "app/templates"), autoescape=True)
    template = env.get_template("rmm/_asset_inventory.html")
    for model, temperature in [("Core Ultra 7 155H", "110.0 °C"), ("Core i5-8250U", "Unknown")]:
        inventory = {"cpu_model": model, "details": {}, "cpu_reference": cpu_catalog.lookup(model)}
        rendered = template.render(asset={"id": 1}, rmm={"inventory": {
            "inventory": inventory, "format_bytes": lambda value: "", "format_uptime": lambda value: ""}})
        assert "CPU release" in rendered
        assert temperature in rendered
        assert "T-junction" in rendered
