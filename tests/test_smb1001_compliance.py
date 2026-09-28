"""Tests for SMB1001 compliance tracking (replacing Essential 8)."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.database import Database
from app.features.compliance import routes as compliance_routes
from app.repositories import smb1001 as smb1001_repo

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "439_smb1001_compliance.sql"
_PARAM = re.compile(r"%\((\w+)\)s")


class _SqliteDb:
    """Minimal stand-in for ``app.core.database.db`` backed by in-memory SQLite.

    Repository SQL uses the MySQL ``%(name)s`` placeholder style, which is
    rewritten to SQLite's ``:name`` style here.
    """

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT)")
        self.conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        self.conn.execute("INSERT INTO companies (id, name) VALUES (1, 'Acme')")
        self.conn.execute("INSERT INTO users (id, email) VALUES (7, 'admin@example.com')")
        adapter = Database.__new__(Database)
        for statement in adapter._split_sql_statements(adapter._adapt_sql_for_sqlite(MIGRATION.read_text())):
            self.conn.execute(statement)
        self.conn.commit()

    def _run(self, sql: str, params):
        return self.conn.execute(_PARAM.sub(r":\1", sql), params or {})

    async def execute(self, sql, params=None):
        cursor = self._run(sql, params)
        self.conn.commit()
        return cursor.lastrowid

    async def fetch_one(self, sql, params=None):
        row = self._run(sql, params).fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql, params=None):
        return [dict(row) for row in self._run(sql, params).fetchall()]


@pytest.fixture
def sqlite_db(monkeypatch):
    fake = _SqliteDb()
    monkeypatch.setattr(smb1001_repo, "db", fake)
    return fake


def _no_essential8(monkeypatch, levels=None):
    monkeypatch.setattr(
        smb1001_repo.essential8_repo,
        "list_essential8_controls",
        AsyncMock(return_value=[{"id": 100 + order, "control_order": order} for order in range(1, 9)]),
    )
    monkeypatch.setattr(
        smb1001_repo.essential8_repo,
        "get_per_maturity_statuses_for_company",
        AsyncMock(return_value=levels or {}),
    )


def test_migration_declares_phase_and_seeds_five_cumulative_tiers():
    adapter = Database.__new__(Database)
    metadata = adapter._migration_metadata(MIGRATION)
    assert metadata["phase"] == "expand"
    assert metadata["legacy"] is False

    db = _SqliteDb()
    tiers = [tuple(row) for row in db.conn.execute("SELECT tier_level, code, attestation FROM smb1001_tiers ORDER BY tier_level")]
    assert tiers == [
        (1, "bronze", "self"),
        (2, "silver", "self"),
        (3, "gold", "self"),
        (4, "platinum", "independent"),
        (5, "diamond", "independent"),
    ]
    per_tier = dict(db.conn.execute("SELECT tier_level, COUNT(*) FROM smb1001_controls GROUP BY tier_level").fetchall())
    assert set(per_tier) == {1, 2, 3, 4, 5}
    assert all(count > 0 for count in per_tier.values())
    domains = {row[0] for row in db.conn.execute("SELECT DISTINCT domain FROM smb1001_controls")}
    assert domains == set(smb1001_repo.DOMAINS)
    codes = {row[0] for row in db.conn.execute("SELECT code FROM smb1001_controls")}
    assert set(smb1001_repo.ESSENTIAL8_MAPPINGS) <= codes
    missing_checks = db.conn.execute(
        "SELECT code FROM smb1001_controls WHERE verification IS NULL OR verification = ''"
    ).fetchall()
    assert missing_checks == []


def test_tier_progress_is_cumulative():
    tiers = [{"tier_level": level, "name": name} for level, name in [(1, "Bronze"), (2, "Silver"), (3, "Gold")]]
    controls = [
        {"id": 1, "tier_level": 1},
        {"id": 2, "tier_level": 1},
        {"id": 3, "tier_level": 2},
        {"id": 4, "tier_level": 3},
    ]
    compliance = {
        1: {"status": "compliant"},
        2: {"status": "not_applicable"},
        4: {"status": "compliant"},
    }

    progress = smb1001_repo.build_tier_progress(tiers, controls, compliance, target_tier=3)

    assert progress["achieved_level"] == 1
    assert progress["achieved_tier"]["name"] == "Bronze"
    assert progress["next_tier"]["name"] == "Silver"
    gold = progress["tiers"][2]
    # Gold's own controls are done, but Silver is not, so Gold is not achieved.
    assert gold["complete"] is True
    assert gold["achieved"] is False
    assert progress["target_done"] == 3
    assert progress["target_total"] == 4
    assert progress["target_remaining"] == 1


def test_tier_progress_with_nothing_done():
    progress = smb1001_repo.build_tier_progress(
        [{"tier_level": 1, "name": "Bronze"}], [{"id": 1, "tier_level": 1}], {}, target_tier=9
    )
    assert progress["achieved_level"] == 0
    assert progress["achieved_tier"] is None
    assert progress["target_tier"] == smb1001_repo.MAX_TIER
    assert progress["tiers"][0]["counts"]["not_started"] == 1


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["compliant"], "compliant"),
        (["compliant", "compliant"], "compliant"),
        (["compliant", "not_started"], "in_progress"),
        (["in_progress"], "in_progress"),
        (["not_started"], None),
        ([], None),
    ],
)
def test_derive_status_from_essential8(statuses, expected):
    assert smb1001_repo.derive_status_from_essential8(statuses) == expected


@pytest.mark.anyio("asyncio")
async def test_save_creates_then_updates_with_audit(sqlite_db):
    control = (await smb1001_repo.list_controls(tier_level=1))[0]

    created = await smb1001_repo.save_company_control_compliance(
        1, control["id"], user_id=7, status="in_progress", notes="Started"
    )
    assert created["status"] == "in_progress"
    assert created["notes"] == "Started"

    updated = await smb1001_repo.save_company_control_compliance(
        1, control["id"], user_id=7, status="compliant", evidence="https://evidence", owner_user_id=7
    )
    assert updated["status"] == "compliant"
    assert updated["notes"] == "Started"
    assert updated["evidence"] == "https://evidence"
    assert updated["owner_user_id"] == 7

    audit = await smb1001_repo.list_control_audit(1, control["id"])
    assert [(row["from_status"], row["to_status"]) for row in audit] == [
        ("in_progress", "compliant"),
        (None, "in_progress"),
    ]


@pytest.mark.anyio("asyncio")
async def test_save_rejects_invalid_status_and_fields(sqlite_db):
    control = (await smb1001_repo.list_controls(tier_level=1))[0]
    with pytest.raises(ValueError):
        await smb1001_repo.save_company_control_compliance(1, control["id"], status="ml3")
    with pytest.raises(ValueError):
        await smb1001_repo.save_company_control_compliance(1, control["id"], maturity_level="ml1")


@pytest.mark.anyio("asyncio")
async def test_essential8_progress_is_converted_once_without_overwriting(sqlite_db, monkeypatch):
    # Essential 8 control ids are 100 + control_order in this fixture.
    _no_essential8(
        monkeypatch,
        levels={
            108: {"ml1": "compliant", "ml2": "in_progress", "ml3": "not_started"},  # backups
            107: {"ml1": "compliant", "ml2": "not_started", "ml3": "not_started"},  # MFA
            102: {"ml1": "compliant", "ml2": "not_started", "ml3": "not_started"},  # patch apps
            106: {"ml1": "in_progress", "ml2": "not_started", "ml3": "not_started"},  # patch OS
        },
    )
    controls = {control["code"]: control for control in await smb1001_repo.list_controls()}
    # Work already recorded in SMB1001 must survive the import.
    await smb1001_repo.save_company_control_compliance(
        1, controls["AM-03"]["id"], status="non_compliant", notes="Manual finding"
    )

    profile = await smb1001_repo.ensure_company_profile(1, user_id=7)
    assert profile["target_tier"] == 1
    assert profile["essential8_imported_at"]

    records = await smb1001_repo.list_company_compliance(1)
    assert records[controls["BR-01"]["id"]]["status"] == "compliant"
    assert records[controls["BR-01"]["id"]]["source"] == "essential8"
    assert "Regular backups ML1" in records[controls["BR-01"]["id"]]["notes"]
    assert records[controls["BR-03"]["id"]]["status"] == "compliant"
    assert records[controls["BR-02"]["id"]]["status"] == "in_progress"
    # Patch applications ML1 compliant but patch OS ML1 only in progress.
    assert records[controls["TM-04"]["id"]]["status"] == "in_progress"
    assert records[controls["AM-03"]["id"]]["status"] == "non_compliant"
    assert records[controls["AM-03"]["id"]]["notes"] == "Manual finding"
    # Unmapped controls are left alone.
    assert controls["TM-01"]["id"] not in records

    # A second visit does not import again.
    import_mock = AsyncMock()
    monkeypatch.setattr(smb1001_repo, "import_essential8_progress", import_mock)
    await smb1001_repo.ensure_company_profile(1)
    import_mock.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_overview_reports_achieved_tier(sqlite_db, monkeypatch):
    _no_essential8(monkeypatch)
    await smb1001_repo.ensure_company_profile(1)
    for control in await smb1001_repo.list_controls(tier_level=1):
        await smb1001_repo.save_company_control_compliance(1, control["id"], status="compliant")
    await smb1001_repo.set_target_tier(1, 3)

    overview = await smb1001_repo.get_company_overview(1)

    progress = overview["progress"]
    assert progress["achieved_tier"]["code"] == "bronze"
    assert progress["next_tier"]["code"] == "silver"
    assert progress["target_tier"] == 3
    assert overview["profile"]["target_tier"] == 3
    assert all(control["status"] == "compliant" for control in overview["controls"] if control["tier_level"] == 1)
    assert all("domain_label" in control for control in overview["controls"])


@pytest.mark.anyio("asyncio")
async def test_compliance_page_renders_smb1001(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template_name"] = template_name
        captured["extra"] = extra
        return extra

    monkeypatch.setattr(
        compliance_routes,
        "_load_compliance_context",
        AsyncMock(return_value=({"id": 1, "is_super_admin": False}, {"is_admin": True}, {"id": 2, "name": "Acme"}, 2, None)),
    )
    monkeypatch.setattr(compliance_routes.smb1001_repo, "ensure_company_profile", AsyncMock(return_value={"target_tier": 1}))
    tiers = [{"tier_level": 1, "code": "bronze", "name": "Bronze"}, {"tier_level": 2, "code": "silver", "name": "Silver"}]
    controls = [
        {"id": 1, "code": "TM-01", "tier_level": 1, "status": "compliant"},
        {"id": 2, "code": "AM-03", "tier_level": 2, "status": "not_started"},
    ]
    progress = smb1001_repo.build_tier_progress(tiers, controls, {1: {"status": "compliant"}}, target_tier=2)
    monkeypatch.setattr(
        compliance_routes.smb1001_repo,
        "get_company_overview",
        AsyncMock(return_value={"profile": {"target_tier": 2}, "tiers": tiers, "controls": controls, "progress": progress}),
    )
    monkeypatch.setattr(compliance_routes.essential8_repo, "list_company_compliance", AsyncMock(return_value=[{"id": 1}]))
    monkeypatch.setattr(compliance_routes.users_repo, "list_users_for_company", AsyncMock(return_value=[]))
    monkeypatch.setattr(compliance_routes, "_main", lambda: SimpleNamespace(_render_template=fake_render_template))

    await compliance_routes.compliance_page(SimpleNamespace())

    assert captured["template_name"] == "compliance/smb1001.html"
    extra = captured["extra"]
    assert extra["title"] == "SMB1001 Compliance"
    assert extra["can_manage"] is True
    assert extra["has_essential8_records"] is True
    assert extra["progress"]["achieved_tier"]["name"] == "Bronze"
    assert [c["code"] for c in extra["controls_by_tier"][2]] == ["AM-03"]
    assert extra["controls_by_tier"][1][0]["show_help"] is False
    assert extra["controls_by_tier"][2][0]["show_help"] is True


def test_smb1001_ticket_text_names_control_and_tier():
    control = {
        "code": "AM-03",
        "name": "MFA on email accounts",
        "domain_label": "Access management",
        "description": "MFA is enforced.",
        "verification": "Platform reports MFA.",
    }
    assert compliance_routes._build_smb1001_ticket_subject(control) == "SMB1001 implementation request: AM-03 MFA on email accounts"
    body = compliance_routes._build_smb1001_ticket_description(
        control=control, tier_name="Silver", company={"name": "Acme"}, user={"email": "a@example.com"}
    )
    assert "Tier: Silver" in body
    assert "How it is checked: Platform reports MFA." in body
    assert compliance_routes._build_smb1001_ticket_reference(3, 9) == "smb1001:control:9:company:3"


def _render_dashboard(monkeypatch, *disabled: str, can_manage: bool = True) -> str:
    import app.main as main_module
    from app.services.component_availability import ComponentAvailability

    policy = ComponentAvailability(disabled_feature_packs=frozenset(disabled))
    monkeypatch.setattr(main_module, "get_component_availability", lambda: policy)
    tiers = [
        {"tier_level": 1, "code": "bronze", "name": "Bronze", "description": "Foundations", "attestation": "self"},
        {"tier_level": 4, "code": "platinum", "name": "Platinum", "description": "Audit", "attestation": "independent"},
    ]
    controls = [
        {
            "id": 1, "code": "TM-01", "tier_level": 1, "name": "Engage IT support", "domain_label": "Technology management",
            "description": "Support engaged.", "verification": "Agreement on file.", "status": "compliant",
            "compliance": {"status": "compliant", "source": "essential8", "notes": "Converted"}, "show_help": False,
        },
        {
            "id": 2, "code": "AM-09", "tier_level": 4, "name": "Phishing-resistant MFA", "domain_label": "Access management",
            "description": "FIDO2.", "verification": "Policy enforced.", "status": "not_started",
            "compliance": None, "show_help": True,
        },
    ]
    progress = smb1001_repo.build_tier_progress(tiers, controls, {1: {"status": "compliant"}}, target_tier=1)
    request = SimpleNamespace(url=SimpleNamespace(path="/compliance", query=""), query_params={})
    return main_module.templates.env.get_template("compliance/smb1001.html").render(
        request=request,
        app_name="MyPortal",
        current_user={"id": 1, "is_super_admin": True, "email_signature": ""},
        is_super_admin=True,
        has_authenticated_user=True,
        active_membership={},
        available_companies=[],
        module_enabled={},
        enabled_module_slugs=[],
        csrf_token="token",
        profile={"target_tier": 1, "essential8_imported_at": "2026-09-28T06:00:00"},
        progress=progress,
        tiers=progress["tiers"],
        controls_by_tier={1: [controls[0]], 4: [controls[1]]},
        domains=smb1001_repo.DOMAINS,
        company_members=[{"id": 7, "email": "admin@example.com"}],
        company={"id": 1, "name": "Acme"},
        has_essential8_records=True,
        can_manage=can_manage,
    )


def test_dashboard_template_renders_tiers_controls_and_actions(monkeypatch):
    body = _render_dashboard(monkeypatch)
    assert "SMB1001 compliance" in body
    assert "Tier 1 · Bronze" in body
    assert "Independent audit" in body
    assert "TM-01" in body and "AM-09" in body
    assert "From Essential 8" in body
    assert "Beyond target" in body
    assert 'action="/compliance/smb1001/2/ticket"' in body
    assert 'action="/compliance/smb1001/1/ticket"' not in body
    assert 'href="/compliance/essential8"' in body
    assert 'id="smb1001-import-e8"' in body
    assert 'id="smb1001-target-tier"' in body
    assert 'data-save-control="1"' in body


def test_dashboard_template_hides_legacy_links_and_editing(monkeypatch):
    body = _render_dashboard(monkeypatch, "essential8", can_manage=False)
    assert 'href="/compliance/essential8"' not in body
    assert 'id="smb1001-import-e8"' not in body
    assert 'id="smb1001-target-tier"' not in body
    assert 'data-save-control="1"' not in body
