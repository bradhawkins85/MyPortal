"""Machine stats filters against stored performance and temperature samples."""
from datetime import datetime
from html import unescape
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.requests import Request

from app.repositories import rmm_stats as repo
from app.services import rmm_stats
from tests.test_rmm_checks import stats_db  # noqa: F401 - fixture
from tests.test_rmm_scripts import _environment, anyio_backend  # noqa: F401 - fixture


@pytest.fixture
async def samples(stats_db, monkeypatch):
    now = datetime(2026, 10, 13, 12)
    monkeypatch.setattr(rmm_stats, "_utcnow", lambda: now)
    agent = {"id": 1, "asset_id": 10, "company_id": 1}
    rows = rmm_stats.thermal_samples(agent, {"sensors": [
        {"id": f"cpu/{i}", "hardware": "Intel CPU", "name": f"Core #{i}", "celsius": 50 + i}
        for i in range(8)
    ] + [{"id": "ssd/1", "hardware": "Samsung SSD", "name": "Composite", "celsius": 40},
         {"id": "board/1", "hardware": '<Board & "sensor">', "name": "Board", "celsius": 30}]}, now)
    for metric, instance in [("cpu_percent", ""), ("memory_used_percent", ""),
                             ("disk_free_percent", "C:"), ("net_in_bps", "Ethernet"),
                             ("net_out_bps", "Ethernet"), ("ping_ms", "gateway")]:
        rows.append({"company_id": 1, "asset_id": 10, "agent_id": 1, "metric": metric,
                     "instance": instance, "value": 20, "sampled_at": now})
    await stats_db.ensure_connected()
    await repo.insert_samples(rows)


@pytest.mark.anyio
@pytest.mark.parametrize("kind, titles", [
    ("cpu", ["CPU"]), ("memory", ["Memory used"]), ("disk", ["Disk free"]),
    ("network", ["Network"]), ("ping", ["Ping"]),
])
async def test_stat_type_isolates_matching_charts(samples, kind, titles):
    context = await rmm_stats.asset_stats_context(10, "7d", kind)
    assert [item["title"] for item in context["charts"]] == titles
    assert context["range"] == "7d" and context["filtered"]


@pytest.mark.anyio
async def test_temperature_device_filters_preserve_every_matching_sensor(samples):
    context = await rmm_stats.asset_stats_context(10, "24h", "temperature", "Intel CPU")
    assert len(context["charts"]) == 8  # No six-series truncation.
    assert all("Intel CPU" in chart["title"] and chart["unit"] == "°C" for chart in context["charts"])
    assert context["hardware_choices"] == ['<Board & "sensor">', "Intel CPU", "Samsung SSD"]
    mixed = await rmm_stats.asset_stats_context(10, "24h", "all", "Samsung SSD")
    assert len(mixed["charts"]) == 6  # Five performance charts plus one SSD sensor.
    assert len(await repo.list_samples(asset_id=10, since=datetime(2026, 10, 12))) == 16
    # Other endpoints' data is excluded, including choices.
    other = await rmm_stats.asset_stats_context(11)
    assert other["charts"] == [] and other["hardware_choices"] == []


@pytest.mark.anyio
async def test_invalid_filters_and_missing_device(samples):
    default = await rmm_stats.asset_stats_context(10, "invalid", "invalid")
    assert default["range"] == "24h" and default["type"] == "all" and not default["filtered"]
    assert len(default["charts"]) == 15
    missing = await rmm_stats.asset_stats_context(10, "30d", "temperature", "Missing SSD")
    assert missing["charts"] == [] and missing["filtered"]
    assert missing["hardware"] == "Missing SSD" and "Missing SSD" in missing["hardware_choices"]


@pytest.mark.anyio
async def test_filter_form_links_escaping_and_empty_state(samples):
    source = (Path(__file__).resolve().parents[1] / "app/templates/assets/detail.html").read_text()
    section = source.split('{% set stats = chk.stats %}', 1)[1].split('</section>', 1)[0]
    template = _environment().from_string(section)
    request = Request({"type": "http", "scheme": "https", "server": ("portal.test", 443),
                       "path": "/assets/10", "query_string":
                       b"stats=7d&stats_type=temperature&stats_hardware=Samsung+SSD&el_level=Error",
                       "headers": []})
    stats = await rmm_stats.asset_stats_context(10, "7d", "temperature", "Samsung SSD")
    html = template.render(stats=stats, asset={"id": 10}, request=request)
    assert 'for="asset-stats-type"' in html and 'for="asset-stats-hardware"' in html
    assert 'value="temperature" selected' in html and 'value="Samsung SSD" selected' in html
    assert '&lt;Board &amp; &#34;sensor&#34;&gt;' in html
    decoded = unescape(html)
    links = [urlsplit(url) for url in re.findall(r'href="([^"]+)"', decoded)]
    queries = [parse_qs(link.query, keep_blank_values=True) for link in links]
    assert all(link.fragment == "machine-stats" for link in links)
    assert {"stats": ["30d"], "stats_type": ["temperature"], "stats_hardware": ["Samsung SSD"],
            "el_level": ["Error"]} in queries
    assert {"stats": ["7d"], "stats_type": ["all"], "stats_hardware": [""],
            "el_level": ["Error"]} in queries
    assert 'name="el_level" value="Error"' in html and 'name="stats" value="7d"' in html
    assert "Temperature · Samsung SSD" in html and "Temperature · Intel CPU" not in html
    empty = await rmm_stats.asset_stats_context(10, "7d", "temperature", "Missing SSD")
    html = template.render(stats=empty, asset={"id": 10}, request=request)
    assert "No stats match these filters" in html and ">Clear</a>" in html
