"""RMM live tools: session lifecycle, agent offers and the cross-worker relay."""
from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_live as live_repo
from app.services import rmm_live
from app.services.tray import hash_token
from tests.test_rmm_scripts import _enrol, anyio_backend, sqlite_db  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _patch_live_repo(sqlite_db, monkeypatch):
    """The live sessions repository talks to the shared ``db`` object; point
    it at the test's SQLite connection like the other RMM repositories."""

    monkeypatch.setattr(live_repo, "db", sqlite_db)
    yield


async def _capable_agent(sqlite_db, *, supported=True):
    _result, agent = await _enrol(sqlite_db)
    await rmm_repo.record_agent_checkin(agent["id"], {"agent_version": "2.0.0", "live_tools_supported": supported})
    return await rmm_repo.get_agent(agent["id"])


@pytest.mark.anyio
async def test_migration_544_present_and_idempotent():
    sql = (ROOT / "migrations/544_rmm_live_sessions.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS rmm_live_sessions" in sql
    assert "CREATE TABLE IF NOT EXISTS rmm_live_frames" in sql
    assert "live_tools_supported" in sql
    # The runner requires a phase header.
    assert "-- phase: expand" in sql


@pytest.mark.anyio
async def test_start_session_requires_a_capable_agent(sqlite_db):
    old = await _capable_agent(sqlite_db, supported=False)
    with pytest.raises(rmm_live.LiveToolsUnsupported):
        await rmm_live.start_session(agent=old, company_id=1, asset_id=10, requested_by_user_id=9)


@pytest.mark.anyio
async def test_session_lifecycle_and_browser_auth(sqlite_db):
    agent = await _capable_agent(sqlite_db)
    started = await rmm_live.start_session(agent=agent, company_id=1, asset_id=10, requested_by_user_id=9)
    assert started.session_id

    # The agent sees the session in its poll, then not again until re-offer.
    offered = await rmm_live.sessions_for_agent(agent)
    assert [item["id"] for item in offered] == [started.session_id]
    assert await rmm_live.sessions_for_agent(agent) == []

    # Only the matching agent may serve it, and only with the right token.
    assert await rmm_live.authorize_agent_session(started.session_id, agent)
    assert await rmm_live.authorize_agent_session(started.session_id, {"id": agent["id"] + 999}) is None
    assert await rmm_live.authorize_browser(started.session_id, started.browser_token)
    assert await rmm_live.authorize_browser(started.session_id, "wrong-token") is None

    await live_repo.mark_agent_connected(started.session_id)
    assert (await live_repo.get_session(started.session_id))["status"] == "connected"

    await rmm_live.close_session(started.session_id, "done")
    assert (await live_repo.get_session(started.session_id))["status"] == "closed"
    # A closed session authorises nobody.
    assert await rmm_live.authorize_browser(started.session_id, started.browser_token) is None


@pytest.mark.anyio
async def test_expired_sessions_are_closed(sqlite_db):
    agent = await _capable_agent(sqlite_db)
    started = await rmm_live.start_session(agent=agent, company_id=1, asset_id=10, requested_by_user_id=9)
    # Force expiry into the past.
    from datetime import datetime, timedelta, timezone
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    await sqlite_db.execute("UPDATE rmm_live_sessions SET expires_at = %s WHERE id = %s", (past, started.session_id))
    await rmm_live.expire_sessions()
    assert (await live_repo.get_session(started.session_id))["status"] == "closed"
    assert await rmm_live.sessions_for_agent(agent) == []


@pytest.mark.anyio
async def test_database_relay_carries_frames_both_ways(sqlite_db):
    """With no Redis configured, frames stored by one worker are read by the
    other and deleted once consumed."""

    agent = await _capable_agent(sqlite_db)
    started = await rmm_live.start_session(agent=agent, company_id=1, asset_id=10, requested_by_user_id=9)
    session_id = started.session_id

    # Two relays standing in for the browser's worker and the agent's worker.
    browser_side = rmm_live.Relay(session_id)
    agent_side = rmm_live.Relay(session_id)
    assert browser_side._redis is None, "test expects the database relay"

    received: list[str] = []

    async def collect(payload: str) -> bool:
        received.append(payload)
        return len(received) < 1

    await browser_side.send("to_agent", {"op": "ping"})
    reader = asyncio.ensure_future(agent_side.receive("to_agent", collect))
    await asyncio.wait_for(reader, timeout=5)
    assert received == ['{"op": "ping"}']

    await browser_side.close()
    await agent_side.close()


@pytest.mark.anyio
async def test_agent_disconnect_reoffers_the_session(sqlite_db):
    agent = await _capable_agent(sqlite_db)
    started = await rmm_live.start_session(agent=agent, company_id=1, asset_id=10, requested_by_user_id=9)
    await live_repo.offer_requested(int(agent["id"]), reoffer_after=rmm_live.REOFFER_AFTER)
    await live_repo.mark_agent_connected(started.session_id)
    await live_repo.mark_agent_disconnected(started.session_id)
    session = await live_repo.get_session(started.session_id)
    assert session["status"] == "requested" and session["offered_at"] is None
    assert [item["id"] for item in await rmm_live.sessions_for_agent(agent)] == [started.session_id]


def _css_rule_selectors(css: str) -> list[str]:
    import re

    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [s.strip() for s in re.findall(r"([^{}]+)\{", without_comments)]


@pytest.mark.anyio
async def test_live_tools_header_class_is_not_reused_by_other_rules():
    """The disk usage share bar once reused ``.rmm-live__bar``, collapsing the
    page header (status, host and Connect button) into a 2px-wide blue box."""

    css = (ROOT / "app/static/css/rmm_live.css").read_text()
    selectors = _css_rule_selectors(css)
    assert selectors.count(".rmm-live__header") == 1
    assert ".rmm-live__share" in selectors
    template = (ROOT / "app/templates/rmm/live_tools.html").read_text()
    assert 'class="rmm-live__header"' in template
    assert "rmm-live__bar" not in template


@pytest.mark.anyio
async def test_live_tools_colour_tokens_are_defined_for_the_dark_theme():
    """``--color-surface-alt`` and friends are not defined by the shared theme,
    so the overview stat tiles fell back to a light grey behind the theme's
    near-white text and the Network/Processes/Uptime values were invisible."""

    import re

    css = (ROOT / "app/static/css/rmm_live.css").read_text()
    shared = "".join(
        p.read_text() for p in (ROOT / "app/static/css").glob("*.css") if p.name != "rmm_live.css"
    )
    used = set(re.findall(r"var\((--color-[a-z-]+)", css))
    for token in used:
        defined = re.search(re.escape(token) + r"\s*:", css) or re.search(
            r"^\s*" + re.escape(token) + r"\s*:", shared, flags=re.M
        )
        assert defined, f"{token} is used by rmm_live.css but never defined"
    assert re.search(r"\.rmm-live__stats dd \{[^}]*color: var\(--color-text", css)


@pytest.mark.anyio
async def test_live_tools_page_retries_hello_until_the_agent_answers():
    """A Redis relay drops frames sent before the agent has dialled in, so the
    browser must repeat ``hello`` rather than wait on the first one forever,
    and the page connects without needing a hidden Connect click."""

    js = (ROOT / "app/static/js/rmm_live.js").read_text()
    assert "async function handshake()" in js
    assert "request('hello', {}, HELLO_RETRY_MS)" in js
    assert "hello = await client.handshake();" in js
    assert "if (canStart) connect();" in js


@pytest.mark.anyio
async def test_live_tools_trend_chart_is_titled_and_has_a_legend():
    """The overview chart once drew two unlabelled lines, so viewers could not
    tell it plotted CPU and memory use or what scale it used."""

    template = (ROOT / "app/templates/rmm/live_tools.html").read_text()
    assert 'aria-labelledby="live-trend-title live-net-trend-title"' in template
    assert "CPU and memory use, last 2 minutes" in template
    assert "rmm-live__swatch--cpu" in template and "rmm-live__swatch--mem" in template
    assert "<span>100%</span>" in template and "<span>0%</span>" in template
    js = (ROOT / "app/static/js/rmm_live.js").read_text()
    # Line colours must come from the same scoped tokens the legend uses.
    assert "getComputedStyle(canvas)" in js


@pytest.mark.anyio
async def test_live_tools_trend_chart_plots_network_in_and_out():
    """Network throughput is plotted as separate In and Out lines on its own
    byte-rate axis, under the CPU and memory chart's 2-minute window."""

    template = (ROOT / "app/templates/rmm/live_tools.html").read_text()
    assert "Network throughput, last 2 minutes" in template
    assert "data-live-net-chart" in template and "data-live-net-axis-max" in template
    assert "rmm-live__swatch--net-in" in template and "rmm-live__swatch--net-out" in template
    js = (ROOT / "app/static/js/rmm_live.js").read_text()
    assert "rx: m.net_rx_bps || 0, tx: m.net_tx_bps || 0" in js
    assert "['rx', '--rmm-live-net-in'" in js and "['tx', '--rmm-live-net-out'" in js
    css = (ROOT / "app/static/css/rmm_live.css").read_text()
    assert "--rmm-live-net-in:" in css and "--rmm-live-net-out:" in css
