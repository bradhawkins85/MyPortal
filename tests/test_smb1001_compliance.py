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
EXTRAS_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "440_smb1001_evidence_help_links_reports.sql"
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
        self.conn.execute(
            "CREATE TABLE marketing_pages (id INTEGER PRIMARY KEY, slug TEXT, title TEXT, is_published INTEGER)"
        )
        self.conn.execute(
            "CREATE TABLE reporting_queries (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE, name TEXT,"
            " description TEXT, sql_query TEXT, is_system INTEGER)"
        )
        adapter = Database.__new__(Database)
        for migration in (MIGRATION, EXTRAS_MIGRATION):
            for statement in adapter._split_sql_statements(adapter._adapt_sql_for_sqlite(migration.read_text())):
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
    assert await smb1001_repo.count_importable_essential8_controls(1) > 0

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
    assert await smb1001_repo.count_importable_essential8_controls(1) == 0

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


def _devices(*flags: tuple[bool, bool]) -> list[dict]:
    return [
        {"id": index, "name": f"PC-{index}", "has_antivirus": av, "has_huntress": huntress}
        for index, (av, huntress) in enumerate(flags, start=1)
    ]


def test_automated_controls_follow_vip_assets_and_email_checks():
    passing = {check_id: "pass" for check_id, _label in smb1001_repo.EMAIL_AUTH_CHECKS}
    results = smb1001_repo.evaluate_automated_controls(
        {"is_vip": True, "devices": _devices((True, True), (True, True)), "email_checks": passing}
    )
    assert {code: (item["status"], item["locked"]) for code, item in results.items()} == {
        "TM-01": ("compliant", True),
        "TM-03": ("compliant", True),
        "TM-07": ("compliant", True),
        "TM-08": ("compliant", True),
    }
    assert "managed service agreement" in results["TM-01"]["reason"]

    results = smb1001_repo.evaluate_automated_controls(
        {
            "is_vip": False,
            "devices": _devices((True, False), (False, True), (True, True)),
            "email_checks": {**passing, "bp_dmarc_records_published": "fail"},
        }
    )
    # Ad hoc customers keep their manual TM-01 status and are offered a letter.
    assert results["TM-01"]["status"] is None and results["TM-01"]["locked"] is False
    assert results["TM-01"]["engagement_letter"] is True
    assert (results["TM-03"]["status"], results["TM-03"]["locked"]) == ("in_progress", True)
    assert "2 of 3 computers report antivirus" in results["TM-03"]["reason"]
    assert "PC-2" in results["TM-03"]["reason"]
    assert (results["TM-07"]["status"], results["TM-07"]["locked"]) == ("in_progress", True)
    assert "PC-1" in results["TM-07"]["reason"]
    assert results["TM-08"]["status"] is None and results["TM-08"]["locked"] is False
    assert "DMARC fail" in results["TM-08"]["reason"]

    # No asset register or unreadable data leaves the manual status in place.
    results = smb1001_repo.evaluate_automated_controls({"is_vip": None, "devices": [], "email_checks": None})
    assert "TM-01" not in results and "TM-08" not in results
    assert results["TM-03"]["status"] is None and results["TM-07"]["status"] is None


def test_device_product_patterns():
    assert smb1001_repo.ANTIVIRUS_PRODUCT_PATTERN.search("Bitdefender Endpoint Security Tools")
    assert smb1001_repo.ANTIVIRUS_PRODUCT_PATTERN.search("Microsoft Defender")
    assert smb1001_repo.ANTIVIRUS_PRODUCT_PATTERN.search("SentinelOne Agent")
    assert not smb1001_repo.ANTIVIRUS_PRODUCT_PATTERN.search("Google Chrome")
    assert smb1001_repo.HUNTRESS_PRODUCT_PATTERN.search("Huntress Agent")
    assert not smb1001_repo.HUNTRESS_PRODUCT_PATTERN.search("Bitdefender")


@pytest.mark.anyio("asyncio")
async def test_load_automation_signals_reads_protected_computers(sqlite_db):
    conn = sqlite_db.conn
    conn.execute("ALTER TABLE companies ADD COLUMN is_vip INTEGER DEFAULT 0")
    conn.execute(
        "CREATE TABLE assets (id INTEGER PRIMARY KEY, company_id INTEGER, name TEXT, type TEXT, asset_type TEXT,"
        " form_factor TEXT, os_name TEXT, machine_type TEXT, archived_at TEXT)"
    )
    conn.execute("CREATE TABLE asset_custom_field_definitions (id INTEGER PRIMARY KEY, name TEXT, field_type TEXT)")
    conn.execute(
        "CREATE TABLE asset_custom_field_values (id INTEGER PRIMARY KEY, asset_id INTEGER,"
        " field_definition_id INTEGER, value_boolean INTEGER)"
    )
    conn.execute("CREATE TABLE tray_devices (id INTEGER PRIMARY KEY, company_id INTEGER, asset_id INTEGER)")
    conn.execute("CREATE TABLE defender_device_status (tray_device_id INTEGER PRIMARY KEY, antivirus_enabled INTEGER)")
    conn.execute(
        "CREATE TABLE m365_best_practice_results (company_id INTEGER, check_id TEXT, status TEXT)"
    )
    conn.executemany(
        "INSERT INTO assets (id, company_id, name, asset_type, archived_at) VALUES (?, 1, ?, ?, ?)",
        [
            (1, "Laptop-A", "laptop", None),
            (2, "Server-B", "server", None),
            (3, "Printer", "printer", None),
            (4, "Old PC", "workstation", "2026-01-01"),
            (5, "Removed PC", "workstation", None),
        ],
    )
    conn.executemany(
        "INSERT INTO asset_custom_field_definitions (id, name, field_type) VALUES (?, ?, 'checkbox')",
        [(1, "Bitdefender Endpoint Security Tools"), (2, "Huntress Agent"), (3, "Archive Asset")],
    )
    conn.executemany(
        "INSERT INTO asset_custom_field_values (asset_id, field_definition_id, value_boolean) VALUES (?, ?, ?)",
        [(1, 1, 1), (1, 2, 1), (2, 1, 0), (5, 3, 1)],
    )
    conn.execute("INSERT INTO tray_devices (id, company_id, asset_id) VALUES (9, 1, 2)")
    conn.execute("INSERT INTO defender_device_status (tray_device_id, antivirus_enabled) VALUES (9, 1)")
    conn.execute("INSERT INTO m365_best_practice_results VALUES (1, 'bp_spf_records_published', 'pass')")
    conn.commit()

    signals = await smb1001_repo.load_automation_signals(1)

    assert signals["is_vip"] is False
    assert signals["devices"] == [
        {"id": 1, "name": "Laptop-A", "has_antivirus": True, "has_huntress": True},
        {"id": 2, "name": "Server-B", "has_antivirus": True, "has_huntress": False},
    ]
    assert signals["email_checks"] == {"bp_spf_records_published": "pass"}


@pytest.mark.anyio("asyncio")
async def test_overview_reports_automated_statuses_without_rewriting_records(sqlite_db, monkeypatch):
    _no_essential8(monkeypatch)
    await smb1001_repo.ensure_company_profile(1)
    controls = {control["code"]: control for control in await smb1001_repo.list_controls()}
    tm01 = controls["TM-01"]
    await smb1001_repo.save_company_control_compliance(
        1, tm01["id"], status="non_compliant", notes="Historical manual status"
    )
    monkeypatch.setattr(
        smb1001_repo,
        "load_automation_signals",
        AsyncMock(return_value={"is_vip": True, "devices": _devices((False, True)), "email_checks": None}),
    )

    overview = await smb1001_repo.get_company_overview(1)

    reported = {control["code"]: control for control in overview["controls"]}
    assert reported["TM-01"]["status"] == "compliant"
    assert reported["TM-01"]["compliance"]["status"] == "compliant"
    assert reported["TM-01"]["status_locked"] is True
    assert reported["TM-03"]["status"] == "in_progress" and reported["TM-03"]["status_locked"] is True
    assert reported["TM-07"]["status"] == "compliant"
    assert reported["TM-08"]["status_locked"] is False
    assert overview["progress"]["tiers"][0]["counts"]["compliant"] >= 1
    # Reporting the automatic status must not rewrite historical attestations.
    stored = await smb1001_repo.get_company_control_compliance(1, tm01["id"])
    assert stored["status"] == "non_compliant"


@pytest.mark.anyio("asyncio")
async def test_non_vip_overview_keeps_manual_tm01_and_offers_letter(sqlite_db, monkeypatch):
    _no_essential8(monkeypatch)
    monkeypatch.setattr(
        smb1001_repo,
        "load_automation_signals",
        AsyncMock(return_value={"is_vip": False, "devices": None, "email_checks": None}),
    )
    overview = await smb1001_repo.get_company_overview(1)
    tm01 = next(control for control in overview["controls"] if control["code"] == "TM-01")
    assert tm01["status"] == "not_started"
    assert tm01["status_locked"] is False
    assert tm01["engagement_letter"] is True


@pytest.mark.anyio("asyncio")
async def test_legacy_essential8_records_ignore_untouched_rows(monkeypatch):
    from app.repositories import essential8 as essential8_repo

    fake = _SqliteDb()
    conn = fake.conn
    conn.execute(
        "CREATE TABLE company_essential8_compliance (company_id INTEGER, status TEXT, evidence TEXT, notes TEXT)"
    )
    conn.execute(
        "CREATE TABLE company_essential8_requirement_compliance"
        " (company_id INTEGER, status TEXT, evidence TEXT, notes TEXT)"
    )
    conn.execute("CREATE TABLE company_essential8_requirement_evidence (company_id INTEGER)")
    conn.execute("INSERT INTO company_essential8_compliance VALUES (1, 'not_started', NULL, '')")
    conn.execute("INSERT INTO company_essential8_requirement_compliance VALUES (1, 'not_started', NULL, NULL)")
    conn.commit()
    monkeypatch.setattr(essential8_repo, "db", fake)

    assert await essential8_repo.company_has_recorded_progress(1) is False

    conn.execute("INSERT INTO company_essential8_requirement_compliance VALUES (1, 'compliant', NULL, NULL)")
    conn.commit()
    assert await essential8_repo.company_has_recorded_progress(1) is True
    assert await essential8_repo.company_has_recorded_progress(2) is False


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
    monkeypatch.setattr(compliance_routes.smb1001_repo, "count_importable_essential8_controls", AsyncMock(return_value=1))
    monkeypatch.setattr(compliance_routes.essential8_repo, "company_has_recorded_progress", AsyncMock(return_value=False))
    monkeypatch.setattr(
        compliance_routes.smb1001_repo,
        "list_evidence_map",
        AsyncMock(return_value={1: [{"id": 5, "control_id": 1, "file_name": "report.pdf"}]}),
    )
    monkeypatch.setattr(
        compliance_routes.smb1001_repo,
        "list_help_links",
        AsyncMock(return_value={
            1: {"help_url": "/marketing/it-support", "recommendation_name": "Managed IT"},
            2: {"help_url": "https://example.com/mfa", "recommendation_name": ""},
        }),
    )
    monkeypatch.setattr(compliance_routes.users_repo, "list_users_for_company", AsyncMock(return_value=[]))
    monkeypatch.setattr(compliance_routes, "_main", lambda: SimpleNamespace(_render_template=fake_render_template))

    await compliance_routes.compliance_page(SimpleNamespace())

    assert captured["template_name"] == "compliance/smb1001.html"
    extra = captured["extra"]
    assert extra["title"] == "SMB1001 Compliance"
    assert extra["can_manage"] is True
    assert extra["essential8_import_count"] == 1
    assert extra["has_essential8_records"] is False
    assert extra["progress"]["achieved_tier"]["name"] == "Bronze"
    assert [c["code"] for c in extra["controls_by_tier"][2]] == ["AM-03"]
    assert extra["controls_by_tier"][1][0]["show_help"] is False
    assert extra["controls_by_tier"][2][0]["show_help"] is True
    # Help links only show for controls that still need work.
    assert extra["controls_by_tier"][1][0]["compliance_help_url"] == ""
    assert extra["controls_by_tier"][2][0]["compliance_help_url"] == "https://example.com/mfa"
    assert extra["controls_by_tier"][2][0]["compliance_help_label"] == "Recommended product or service"
    assert extra["controls_by_tier"][1][0]["evidence_files"][0]["file_name"] == "report.pdf"


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


def _render_dashboard(
    monkeypatch,
    *disabled: str,
    can_manage: bool = True,
    essential8_import_count: int = 1,
    has_essential8_records: bool = True,
    engagement_letter: bool = False,
) -> str:
    import app.main as main_module
    from app.services.component_availability import ComponentAvailability

    policy = ComponentAvailability(disabled_feature_packs=frozenset(disabled))
    monkeypatch.setattr(main_module, "get_component_availability", lambda: policy)
    # Other tests can replace template globals; pin the one this page uses.
    monkeypatch.setitem(main_module.templates.env.globals, "feature_pack_available", main_module._feature_pack_available)
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
            "id": 3, "code": "TM-01", "tier_level": 1, "name": "Engage IT support", "domain_label": "Technology management",
            "description": "Support engaged.", "verification": "Agreement on file.", "status": "not_started",
            "compliance": None, "show_help": True, "engagement_letter": engagement_letter,
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
        controls_by_tier={1: [controls[0], controls[1]], 4: [controls[2]]},
        domains=smb1001_repo.DOMAINS,
        company_members=[{"id": 7, "email": "admin@example.com"}],
        company={"id": 1, "name": "Acme"},
        has_essential8_records=has_essential8_records,
        essential8_import_count=essential8_import_count,
        current_user_id=7,
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


def test_dashboard_template_hides_legacy_button_without_records(monkeypatch):
    body = _render_dashboard(monkeypatch, has_essential8_records=False)
    assert 'href="/compliance/essential8"' not in body
    assert "Legacy Essential 8 records" not in body


def test_dashboard_template_offers_engagement_letter_to_ad_hoc_customers(monkeypatch):
    assert 'action="/compliance/smb1001/engagement-letter"' not in _render_dashboard(monkeypatch)
    body = _render_dashboard(monkeypatch, engagement_letter=True)
    assert 'action="/compliance/smb1001/engagement-letter"' in body
    assert '<option value="7" selected>admin@example.com</option>' in body
    assert "Send letter of engagement" in body
    assert 'action="/compliance/smb1001/engagement-letter"' not in _render_dashboard(
        monkeypatch, engagement_letter=True, can_manage=False
    )


def test_dashboard_template_hides_legacy_links_and_editing(monkeypatch):
    body = _render_dashboard(monkeypatch, "essential8", can_manage=False)
    assert 'href="/compliance/essential8"' not in body
    assert 'id="smb1001-import-e8"' not in body
    assert 'id="smb1001-target-tier"' not in body
    assert 'data-save-control="1"' not in body


def test_dashboard_template_hides_import_when_nothing_is_importable(monkeypatch):
    body = _render_dashboard(monkeypatch, essential8_import_count=0)
    assert 'id="smb1001-import-e8"' not in body


# ---------------------------------------------------------------------------
# Evidence, help links, recommendations and reports
# ---------------------------------------------------------------------------


def test_extras_migration_declares_phase_and_reporting_queries():
    adapter = Database.__new__(Database)
    metadata = adapter._migration_metadata(EXTRAS_MIGRATION)
    assert metadata["phase"] == "expand"
    db = _SqliteDb()
    slugs = {row[0] for row in db.conn.execute("SELECT slug FROM reporting_queries")}
    assert slugs == {"report-smb1001-compliance-progress", "stat-strip-report-smb1001"}
    for (sql,) in db.conn.execute("SELECT sql_query FROM reporting_queries"):
        assert "{{current.company}}" in sql
        # The stored query must run once the company variable is substituted.
        db.conn.execute(sql.replace("{{current.company}}", "1")).fetchall()


async def _control_id(code: str) -> int:
    return next(int(c["id"]) for c in await smb1001_repo.list_controls() if c["code"] == code)


@pytest.mark.anyio("asyncio")
async def test_evidence_versions_and_delete_promotes_previous(sqlite_db):
    control_id = await _control_id("AM-03")
    first = await smb1001_repo.add_evidence(
        company_id=1, control_id=control_id, title="MFA report", file_name="a.pdf",
        file_path="compliance/smb1001/a.evidence", uploaded_by=7, file_size_bytes=10,
    )
    second = await smb1001_repo.add_evidence(
        company_id=1, control_id=control_id, title="MFA report v2", file_name="b.pdf",
        file_path="compliance/smb1001/b.evidence", uploaded_by=7,
    )
    assert (first["version_number"], second["version_number"]) == (1, 2)

    files = (await smb1001_repo.list_evidence_map(1))[control_id]
    assert [(f["version_number"], f["is_current"]) for f in files] == [(2, True), (1, False)]
    # Another company cannot see or fetch it.
    assert await smb1001_repo.get_evidence(2, second["id"]) is None

    deleted = await smb1001_repo.delete_evidence(1, second["id"], user_id=7)
    assert deleted["file_path"] == "compliance/smb1001/b.evidence"
    files = (await smb1001_repo.list_evidence_map(1, control_id=control_id))[control_id]
    assert [(f["version_number"], f["is_current"]) for f in files] == [(1, True)]
    actions = [row["action"] for row in await smb1001_repo.list_control_audit(1, control_id)]
    assert actions.count("evidence_upload") == 2 and "evidence_delete" in actions


@pytest.mark.anyio("asyncio")
async def test_help_links_prefer_external_url_and_need_published_pages(sqlite_db):
    sqlite_db.conn.execute("INSERT INTO marketing_pages (id, slug, title, is_published) VALUES (3, 'edr', 'EDR', 1)")
    sqlite_db.conn.execute("INSERT INTO marketing_pages (id, slug, title, is_published) VALUES (4, 'draft', 'Draft', 0)")
    edr, mfa, draft, cleared = [await _control_id(code) for code in ("TM-07", "AM-03", "TM-01", "TM-02")]
    await smb1001_repo.replace_help_links({
        edr: {"marketing_page_id": 3, "recommendation_name": "Managed EDR", "external_url": ""},
        mfa: {"marketing_page_id": 3, "recommendation_name": "", "external_url": "https://example.com/mfa"},
        draft: {"marketing_page_id": 4, "recommendation_name": "Draft", "external_url": ""},
        cleared: {"marketing_page_id": None, "recommendation_name": "Old", "external_url": ""},
    })
    await smb1001_repo.replace_help_links({cleared: {"marketing_page_id": None, "recommendation_name": "", "external_url": ""}})

    links = await smb1001_repo.list_help_links()
    assert links[edr]["help_url"] == "/marketing/edr"
    assert links[mfa]["help_url"] == "https://example.com/mfa"
    assert links[draft]["help_url"] == ""
    assert cleared not in links


@pytest.mark.anyio("asyncio")
async def test_recommendations_cover_outstanding_controls_to_target(sqlite_db, monkeypatch):
    _no_essential8(monkeypatch)
    await smb1001_repo.ensure_company_profile(1)
    await smb1001_repo.set_target_tier(1, 2)
    bronze = await smb1001_repo.list_controls(tier_level=1)
    for control in bronze[1:]:
        await smb1001_repo.save_company_control_compliance(1, control["id"], status="compliant")
    await smb1001_repo.replace_help_links({bronze[0]["id"]: {"recommendation_name": "IT support plan", "external_url": "https://example.com/it"}})

    monkeypatch.setattr(
        smb1001_repo,
        "load_automation_signals",
        AsyncMock(return_value={"is_vip": True, "devices": None, "email_checks": None}),
    )

    result = await smb1001_repo.list_recommendations(1)

    tiers = {row["tier_level"] for row in result["recommendations"]}
    # TM-01 is covered by the VIP managed service agreement and is no longer
    # an outstanding Bronze recommendation.
    assert tiers == {2}
    assert all(row["control_id"] != bronze[0]["id"] for row in result["recommendations"])
    assert result["total"] == len(await smb1001_repo.list_controls(tier_level=2))
    assert result["horizon_tier"] == "Silver"


@pytest.mark.anyio("asyncio")
async def test_report_builders(sqlite_db, monkeypatch):
    from app.services import reports

    _no_essential8(monkeypatch)
    for control in await smb1001_repo.list_controls(tier_level=1):
        await smb1001_repo.save_company_control_compliance(1, control["id"], status="compliant")
    control_id = await _control_id("AM-03")
    await smb1001_repo.save_company_control_compliance(1, control_id, status="non_compliant")
    await smb1001_repo.add_evidence(
        company_id=1, control_id=control_id, title="Report", file_name="r.pdf", file_path="compliance/smb1001/r.evidence"
    )

    summary = await reports._build_smb1001(1)
    assert summary["achieved_tier"] == "Bronze"
    assert summary["next_tier"] == "Silver"
    silver = summary["tiers"][1]
    assert silver["non_compliant"] == 1 and silver["done"] == 0
    assert not reports._section_is_empty("smb1001", summary)

    detail = await reports._build_smb1001_detail(1)
    row = next(item for item in detail["controls"] if item["code"] == "AM-03")
    assert row["status"] == "non_compliant" and row["evidence_file_count"] == 1

    recommendations = await reports._build_smb1001_recommendations(1)
    assert recommendations["horizon_tier"] == "Silver"
    assert all(item["tier"] == "Silver" for item in recommendations["recommendations"])


def test_report_sections_follow_component_switches(monkeypatch):
    from app.services import reports
    import app.services.component_availability as availability_module
    from app.services.component_availability import ComponentAvailability

    keys = [section.key for section in reports.REPORT_SECTIONS]
    assert keys.index("smb1001") < keys.index("essential8")
    policy = ComponentAvailability(disabled_feature_packs=frozenset({"essential8"}))
    monkeypatch.setattr(availability_module, "get_component_availability", lambda: policy)
    assert reports._section_component_available("smb1001")
    assert reports._section_component_available("smb1001_recommendations")
    assert not reports._section_component_available("essential8_ml2")
    assert reports._section_component_available("licenses")


def test_default_layout_and_queries_use_smb1001(monkeypatch):
    import asyncio

    from app.services import company_report_layout
    import app.services.component_availability as availability_module
    from app.services.component_availability import ComponentAvailability

    slugs = [column["slug"] for row in company_report_layout.default_layout() for column in row["columns"]]
    assert "report-smb1001-compliance-progress" in slugs
    assert "report-essential-8-compliance-progress" not in slugs

    async def fake_list_queries():
        return [{"slug": "report-smb1001-compliance-progress"}, {"slug": "stat-strip-report-smb1001"}, {"slug": "report-licenses"}]

    monkeypatch.setattr(company_report_layout.reporting_repo, "list_queries", fake_list_queries)
    policy = ComponentAvailability(disabled_feature_packs=frozenset({"smb1001"}))
    monkeypatch.setattr(availability_module, "get_component_availability", lambda: policy)
    assert [q["slug"] for q in asyncio.run(company_report_layout.available_queries())] == ["report-licenses"]


def test_report_section_templates_render():
    import app.main as main_module

    env = main_module.templates.env
    summary = {
        "achieved_tier": "Bronze", "next_tier": "Silver", "next_tier_remaining": 3, "target_tier": "Gold",
        "target_done": 7, "target_total": 31, "target_percentage": 22.6, "total": 46,
        "tiers": [{"tier_level": 1, "name": "Bronze", "attestation": "self", "total": 7, "done": 7, "in_progress": 0,
                   "non_compliant": 0, "not_started": 0, "percentage": 100.0, "achieved": True}],
    }
    body = env.get_template("reports/_sections/smb1001.html").render(section=SimpleNamespace(data=summary))
    assert "Tier achieved" in body and "Bronze" in body and "Self-attested" in body
    detail = {"total": 1, "controls": [{"code": "AM-03", "name": "MFA", "tier": "Silver", "domain": "Access management",
                                        "status": "non_compliant", "evidence_file_count": 2}]}
    body = env.get_template("reports/_sections/smb1001_detail.html").render(section=SimpleNamespace(detail_data=detail))
    assert "AM-03" in body and "Non Compliant" in body
    recs = {"total": 1, "horizon_tier": "Silver", "recommendations": [
        {"code": "AM-03", "control": "MFA", "tier": "Silver", "status": "in_progress", "recommendation": "MFA rollout", "url": "/marketing/mfa"}
    ]}
    body = env.get_template("reports/_sections/smb1001_recommendations.html").render(section=SimpleNamespace(data=recs))
    assert 'href="/marketing/mfa"' in body and "to reach Silver" in body


def test_attestation_pdf_template_includes_level_evidence_and_review_dates():
    import app.main as main_module

    body = main_module.templates.env.get_template("compliance/smb1001_attestation_pdf.html").render(
        company={"name": "Acme & Co"},
        generated_at=__import__("datetime").datetime(2026, 9, 30, 12, 0),
        progress={
            "achieved_tier": {"name": "Bronze"},
            "target_tier": 2,
            "target_percentage": 50,
            "target_done": 5,
            "target_total": 10,
            "overall_percentage": 25,
            "overall_done": 5,
            "overall_total": 20,
            "tiers": [{"tier_level": 2, "name": "Silver"}],
        },
        controls=[{
            "code": "AM-03", "tier_level": 1, "name": "MFA", "domain_label": "Access management",
            "status": "compliant", "record": {"last_reviewed_date": "2026-09-29", "target_compliance_date": None,
                                                     "updated_at": "2026-09-30T10:00:00", "evidence": "Configuration checked", "notes": "Reviewed"},
            "evidence_files": [{"title": "MFA configuration", "version_number": 2,
                                "description": "Admin export", "created_at": "2026-09-28T10:00:00"}],
        }],
    )
    assert "Current achieved level" in body and "Bronze" in body
    assert "MFA configuration" in body and "Configuration checked" in body
    assert "2026-09-29" in body and "Not recorded" in body


def test_attestation_pdf_filename_is_safe():
    filename = compliance_routes._safe_attestation_filename(' Acme / "North" ')
    assert filename.startswith("SMB1001_attestation_Acme_North_")
    assert filename.endswith(".pdf")
    assert '"' not in filename and "/" not in filename


# ---------------------------------------------------------------------------
# Evidence API
# ---------------------------------------------------------------------------


def _upload(data: bytes, filename: str, content_type: str):
    import io

    from fastapi import UploadFile
    from starlette.datastructures import Headers

    return UploadFile(file=io.BytesIO(data), filename=filename, headers=Headers({"content-type": content_type}))


@pytest.fixture
def evidence_api(sqlite_db, monkeypatch, tmp_path):
    from app.api.routes import smb1001 as api

    monkeypatch.setattr(api, "_assert_company_compliance_access", AsyncMock())
    monkeypatch.setattr(api, "_private_uploads_root", lambda: tmp_path)
    audit = AsyncMock()
    monkeypatch.setattr(api.audit_service, "record", audit)
    return SimpleNamespace(api=api, root=tmp_path, audit=audit)


@pytest.mark.anyio("asyncio")
async def test_evidence_upload_download_and_delete(evidence_api):
    api = evidence_api.api
    control_id = await _control_id("AM-03")
    user = {"id": 7}

    record = await api.upload_control_evidence(
        1, control_id, SimpleNamespace(), title=" MFA report ", description=None,
        evidence_file=_upload(b"<script>alert(1)</script>", "../../evil.html", "text/html"), user=user,
    )
    assert record["title"] == "MFA report"
    assert record["file_name"] == "evil.html"
    assert record["file_path"].startswith("compliance/smb1001/") and record["file_path"].endswith(".evidence")
    stored = evidence_api.root / record["file_path"]
    assert stored.read_bytes() == b"<script>alert(1)</script>"

    response = await api.download_evidence(1, record["id"], user=user)
    # Active content is never served inline with its own type.
    assert response.media_type == "application/octet-stream"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"

    with pytest.raises(api.HTTPException) as missing:
        await api.download_evidence(2, record["id"], user=user)
    assert missing.value.status_code == 404

    result = await api.delete_evidence(1, record["id"], SimpleNamespace(), user=user)
    assert result == {"deleted": True, "id": record["id"]}
    assert not stored.exists()
    assert [call.kwargs["action"] for call in evidence_api.audit.await_args_list] == [
        "smb1001.evidence.upload",
        "smb1001.evidence.delete",
    ]


@pytest.mark.anyio("asyncio")
async def test_evidence_upload_rejects_empty_oversized_and_unknown_control(evidence_api, monkeypatch):
    api = evidence_api.api
    control_id = await _control_id("AM-03")
    user = {"id": 7}

    with pytest.raises(api.HTTPException) as empty:
        await api.upload_control_evidence(
            1, control_id, SimpleNamespace(), title="Empty", description=None,
            evidence_file=_upload(b"", "e.pdf", "application/pdf"), user=user,
        )
    assert empty.value.status_code == 400

    monkeypatch.setattr(api, "_MAX_EVIDENCE_SIZE_BYTES", 4)
    with pytest.raises(api.HTTPException) as too_big:
        await api.upload_control_evidence(
            1, control_id, SimpleNamespace(), title="Big", description=None,
            evidence_file=_upload(b"12345", "b.pdf", "application/pdf"), user=user,
        )
    assert too_big.value.status_code == 413

    with pytest.raises(api.HTTPException) as unknown:
        await api.upload_control_evidence(
            1, 99999, SimpleNamespace(), title="X", description=None,
            evidence_file=_upload(b"1", "x.pdf", "application/pdf"), user=user,
        )
    assert unknown.value.status_code == 404
    # Nothing is left on disk from rejected uploads.
    assert not any(path.is_file() for path in evidence_api.root.rglob("*"))
    assert await smb1001_repo.list_evidence_map(1) == {}


@pytest.mark.anyio("asyncio")
async def test_download_refuses_paths_outside_evidence_folder(evidence_api):
    api = evidence_api.api
    control_id = await _control_id("AM-03")
    (evidence_api.root / "secret.txt").write_text("secret")
    record = await smb1001_repo.add_evidence(
        company_id=1, control_id=control_id, title="x", file_name="x", file_path="compliance/smb1001/../../secret.txt"
    )
    with pytest.raises(api.HTTPException) as refused:
        await api.download_evidence(1, record["id"], user={"id": 7})
    assert refused.value.status_code == 404


# ---------------------------------------------------------------------------
# Marketing help links admin
# ---------------------------------------------------------------------------


class _FormRequest(SimpleNamespace):
    async def form(self):
        return self._form


@pytest.mark.anyio("asyncio")
async def test_marketing_help_links_save_validates_and_stores(sqlite_db, monkeypatch):
    from fastapi import HTTPException

    from app.features.marketing import routes as marketing_routes

    monkeypatch.setattr(
        marketing_routes, "_require_marketing_access", AsyncMock(return_value=({"id": 7, "is_super_admin": True}, None))
    )
    monkeypatch.setattr(marketing_routes.marketing_repo, "list_pages", AsyncMock(return_value=[{"id": 3}]))
    monkeypatch.setattr(marketing_routes.audit_service, "record", AsyncMock())
    edr = await _control_id("TM-07")

    bad = _FormRequest(_form={f"external_url_{edr}": "javascript:alert(1)"})
    with pytest.raises(HTTPException) as exc:
        await marketing_routes.admin_marketing_update_smb1001_help_links(bad)
    assert exc.value.status_code == 400

    unknown_page = _FormRequest(_form={f"control_{edr}": "9"})
    with pytest.raises(HTTPException):
        await marketing_routes.admin_marketing_update_smb1001_help_links(unknown_page)

    sqlite_db.conn.execute("INSERT INTO marketing_pages (id, slug, title, is_published) VALUES (3, 'edr', 'EDR', 1)")
    good = _FormRequest(_form={f"control_{edr}": "3", f"recommendation_name_{edr}": "Managed EDR"})
    response = await marketing_routes.admin_marketing_update_smb1001_help_links(good)
    assert response.status_code == 303
    links = await smb1001_repo.list_help_links()
    assert links[edr]["recommendation_name"] == "Managed EDR"
    assert links[edr]["help_url"] == "/marketing/edr"

    non_admin = AsyncMock(return_value=({"id": 8, "is_super_admin": False}, None))
    monkeypatch.setattr(marketing_routes, "_require_marketing_access", non_admin)
    with pytest.raises(HTTPException) as forbidden:
        await marketing_routes.admin_marketing_update_smb1001_help_links(good)
    assert forbidden.value.status_code == 403


def test_marketing_help_links_template_renders(monkeypatch):
    import app.main as main_module
    from app.services.component_availability import ComponentAvailability

    monkeypatch.setattr(main_module, "get_component_availability", lambda: ComponentAvailability(disabled_feature_packs=frozenset()))
    monkeypatch.setitem(main_module.templates.env.globals, "feature_pack_available", main_module._feature_pack_available)
    body = main_module.templates.env.get_template("admin/marketing_smb1001_help_links.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/admin/marketing/smb1001-help-links", query=""), query_params={}),
        app_name="MyPortal",
        current_user={"id": 1, "is_super_admin": True, "email_signature": ""},
        is_super_admin=True,
        has_authenticated_user=True,
        active_membership={},
        available_companies=[],
        module_enabled={},
        enabled_module_slugs=[],
        csrf_token="token",
        marketing_pages=[{"id": 3, "title": "EDR", "is_published": False}],
        smb1001_help_tiers=[{
            "tier_level": 3, "name": "Gold", "description": "Gold tier",
            "controls": [{"id": 20, "code": "TM-07", "name": "EDR", "domain_label": "Technology management",
                          "selected_marketing_page_id": 3, "recommendation_name": "Managed EDR", "external_url": ""}],
        }],
    )
    assert 'name="control_20"' in body
    assert "EDR (unpublished)" in body
    assert 'value="Managed EDR"' in body


def _letter_request(form: dict):
    async def read_form():
        return form

    return SimpleNamespace(form=read_form)


def _patch_letter_route(monkeypatch, *, company: dict, membership: dict | None = None, sent: bool = True):
    user = {"id": 7, "email": "tech@example.com", "first_name": "Tess", "last_name": "Tech", "is_super_admin": False}
    monkeypatch.setattr(
        compliance_routes,
        "_load_compliance_context",
        AsyncMock(return_value=(user, membership or {"is_admin": True}, company, 1, None)),
    )
    monkeypatch.setattr(
        compliance_routes.users_repo,
        "list_users_for_company",
        AsyncMock(return_value=[{"id": 9, "email": "owner@acme.test", "first_name": "Olive", "last_name": "Owner"}]),
    )
    monkeypatch.setattr(compliance_routes.message_templates_service, "get_template_by_slug", AsyncMock(return_value=None))
    monkeypatch.setattr(compliance_routes.smb1001_repo, "get_control_by_code", AsyncMock(return_value={"id": 11, "code": "TM-01"}))
    monkeypatch.setattr(
        compliance_routes.smb1001_repo,
        "get_company_control_compliance",
        AsyncMock(return_value={"status": "not_started", "evidence": "Old contract"}),
    )
    save = AsyncMock(return_value={"id": 3, "status": "compliant"})
    monkeypatch.setattr(compliance_routes.smb1001_repo, "save_company_control_compliance", save)
    send = AsyncMock(return_value=(sent, None))
    monkeypatch.setattr(compliance_routes.email_service, "send_email", send)
    monkeypatch.setattr(compliance_routes.audit_service, "record", AsyncMock())
    return send, save


@pytest.mark.anyio("asyncio")
async def test_engagement_letter_is_emailed_and_recorded_as_evidence(monkeypatch):
    send, save = _patch_letter_route(monkeypatch, company={"id": 1, "name": "Acme & Co", "is_vip": 0})

    response = await compliance_routes.compliance_send_engagement_letter(_letter_request({"recipient_user_id": "9"}))

    assert response.status_code == 303
    kwargs = send.await_args.kwargs
    assert kwargs["recipients"] == ["owner@acme.test"]
    assert kwargs["subject"] == "Letter of engagement for IT support - Acme & Co"
    assert "Dear Olive Owner," in kwargs["html_body"]
    assert "Acme &amp; Co engages Hawkins IT Solutions" in kwargs["html_body"]
    assert "Tess Tech" in kwargs["text_body"] and "<p>" not in kwargs["text_body"]
    save_kwargs = save.await_args.kwargs
    assert save.await_args.args == (1, 11)
    assert save_kwargs["status"] == "compliant"
    assert save_kwargs["source"] == "engagement_letter"
    assert save_kwargs["evidence"].startswith("Old contract\nLetter of engagement emailed to owner@acme.test on ")


@pytest.mark.anyio("asyncio")
async def test_engagement_letter_is_refused_for_vip_non_admin_and_outsiders(monkeypatch):
    send, save = _patch_letter_route(monkeypatch, company={"id": 1, "name": "Acme", "is_vip": 1})
    await compliance_routes.compliance_send_engagement_letter(_letter_request({"recipient_user_id": "9"}))

    _patch_letter_route(monkeypatch, company={"id": 1, "name": "Acme", "is_vip": 0}, membership={"is_admin": False})
    await compliance_routes.compliance_send_engagement_letter(_letter_request({"recipient_user_id": "9"}))
    send.assert_not_awaited()

    send, save = _patch_letter_route(monkeypatch, company={"id": 1, "name": "Acme", "is_vip": 0})
    # Only members of the company can receive the letter.
    await compliance_routes.compliance_send_engagement_letter(_letter_request({"recipient_user_id": "42"}))
    send.assert_not_awaited()
    save.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_failed_engagement_letter_is_not_recorded(monkeypatch):
    send, save = _patch_letter_route(monkeypatch, company={"id": 1, "name": "Acme", "is_vip": 0}, sent=False)
    await compliance_routes.compliance_send_engagement_letter(_letter_request({"recipient_user_id": "9"}))
    send.assert_awaited_once()
    save.assert_not_awaited()


def test_engagement_letter_migration_seeds_editable_templates():
    migration = Path(__file__).resolve().parent.parent / "migrations" / "447_smb1001_engagement_letter_template.sql"
    adapter = Database.__new__(Database)
    assert adapter._migration_metadata(migration)["phase"] == "expand"
    text = migration.read_text()
    assert "'smb1001-engagement-letter'" in text and "'smb1001-engagement-letter-subject'" in text
