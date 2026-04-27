"""
Demo Company Seeding Service.

Creates and tears down a "Demo Company" populated with realistic sample data so
evaluators and new users can explore MyPortal without having to configure
real integrations.

Data seeded per run
-------------------
* 1 company  : "Demo Company"  (is_demo=1)
* 8 staff    : variety of departments / job titles
* 3 shop products + 4 orders
* 5 licenses
* 3 subscriptions (requires shop products with subscription_category)
* 6 assets  (workstations and servers)
* 8 M365 best-practice check results
* 5 compliance check assignments (CUSTOM category)
* 8 Essential 8 compliance entries
* 1 business continuity plan  (legacy business_continuity_plans table)
* 3 issues with company status assignments

All seeding is idempotent – if a demo company already exists the function is a
no-op.  Call ``remove_demo_data`` to purge everything, then ``seed_demo_data``
to re-create from scratch.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.core.database import db
from app.core.logging import log_error, log_info
from app.repositories import companies as company_repo
from app.repositories import staff as staff_repo
from app.repositories import assets as assets_repo
from app.repositories import licenses as licenses_repo
from app.repositories import shop as shop_repo
from app.repositories import compliance_checks as compliance_repo
from app.repositories import essential8 as e8_repo
from app.repositories import issues as issues_repo
from app.repositories import business_continuity_plans as bc_plans_repo
from app.repositories import m365_best_practices as bp_repo
from app.repositories import subscriptions as subscriptions_repo
from app.repositories import subscription_categories as sub_cat_repo

_DEMO_COMPANY_NAME = "Demo Company"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_demo_company() -> dict[str, Any] | None:
    """Return the existing demo company row, or None."""
    row = await db.fetch_one(
        "SELECT * FROM companies WHERE is_demo = 1 ORDER BY id LIMIT 1"
    )
    return dict(row) if row else None


async def _get_first_super_admin_id() -> int | None:
    """Return the id of the first super-admin user (used as created_by)."""
    row = await db.fetch_one(
        "SELECT id FROM users WHERE is_super_admin = 1 ORDER BY id LIMIT 1"
    )
    if row:
        return int(row["id"])
    row = await db.fetch_one("SELECT id FROM users ORDER BY id LIMIT 1")
    return int(row["id"]) if row else None


def _today() -> date:
    return datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def is_demo_seeded() -> bool:
    """Return True when at least one demo company exists in the database."""
    company = await _get_demo_company()
    return company is not None


async def seed_demo_data(seeded_by_user_id: int | None = None) -> dict[str, Any]:
    """
    Seed the Demo Company and all related demo records.

    This function is idempotent – it returns immediately (with a ``skipped``
    flag) if a demo company already exists.

    Returns a summary dict describing what was created.
    """
    if await is_demo_seeded():
        log_info("Demo data already seeded – skipping")
        return {"skipped": True, "reason": "Demo company already exists"}

    if seeded_by_user_id is None:
        seeded_by_user_id = await _get_first_super_admin_id()

    stats: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 1. Company
    # ------------------------------------------------------------------
    company = await company_repo.create_company(
        name=_DEMO_COMPANY_NAME,
        address="123 Demo Street, Demo City, DS 1000",
        is_demo=1,
    )
    company_id: int = company["id"]
    stats["company_id"] = company_id
    log_info("Demo company created", company_id=company_id)

    # ------------------------------------------------------------------
    # 2. Staff
    # ------------------------------------------------------------------
    today = _today()
    demo_staff = [
        {
            "first_name": "Alice",
            "last_name": "Anderson",
            "email": "alice.anderson@demo.invalid",
            "job_title": "IT Manager",
            "department": "IT",
            "date_onboarded": today - timedelta(days=730),
            "enabled": True,
            "approval_status": "approved",
            "onboarding_status": "complete",
            "onboarding_complete": True,
        },
        {
            "first_name": "Bob",
            "last_name": "Baker",
            "email": "bob.baker@demo.invalid",
            "job_title": "Systems Administrator",
            "department": "IT",
            "date_onboarded": today - timedelta(days=540),
            "enabled": True,
            "approval_status": "approved",
            "onboarding_status": "complete",
            "onboarding_complete": True,
        },
        {
            "first_name": "Carol",
            "last_name": "Clarke",
            "email": "carol.clarke@demo.invalid",
            "job_title": "Finance Manager",
            "department": "Finance",
            "date_onboarded": today - timedelta(days=900),
            "enabled": True,
            "approval_status": "approved",
            "onboarding_status": "complete",
            "onboarding_complete": True,
        },
        {
            "first_name": "David",
            "last_name": "Davies",
            "email": "david.davies@demo.invalid",
            "job_title": "Developer",
            "department": "Engineering",
            "date_onboarded": today - timedelta(days=365),
            "enabled": True,
            "approval_status": "approved",
            "onboarding_status": "complete",
            "onboarding_complete": True,
        },
        {
            "first_name": "Eve",
            "last_name": "Evans",
            "email": "eve.evans@demo.invalid",
            "job_title": "HR Coordinator",
            "department": "Human Resources",
            "date_onboarded": today - timedelta(days=180),
            "enabled": True,
            "approval_status": "approved",
            "onboarding_status": "complete",
            "onboarding_complete": True,
        },
        {
            "first_name": "Frank",
            "last_name": "Foster",
            "email": "frank.foster@demo.invalid",
            "job_title": "Sales Executive",
            "department": "Sales",
            "date_onboarded": today - timedelta(days=270),
            "enabled": True,
            "approval_status": "approved",
            "onboarding_status": "complete",
            "onboarding_complete": True,
        },
        {
            "first_name": "Grace",
            "last_name": "Green",
            "email": "grace.green@demo.invalid",
            "job_title": "Marketing Specialist",
            "department": "Marketing",
            "date_onboarded": today - timedelta(days=120),
            "enabled": True,
            "approval_status": "approved",
            "onboarding_status": "complete",
            "onboarding_complete": True,
        },
        {
            "first_name": "Harry",
            "last_name": "Hughes",
            "email": "harry.hughes@demo.invalid",
            "job_title": "CEO",
            "department": "Executive",
            "date_onboarded": today - timedelta(days=1460),
            "enabled": True,
            "approval_status": "approved",
            "onboarding_status": "complete",
            "onboarding_complete": True,
        },
    ]
    created_staff = 0
    for s in demo_staff:
        try:
            await staff_repo.create_staff(company_id=company_id, source="demo", **s)
            created_staff += 1
        except Exception as exc:  # pragma: no cover
            log_error("Failed to create demo staff member", email=s["email"], error=str(exc))
    stats["staff"] = created_staff

    # ------------------------------------------------------------------
    # 3. Shop products (needed for orders & subscriptions)
    # ------------------------------------------------------------------
    demo_products: list[dict[str, Any]] = []
    product_specs = [
        {
            "name": "Demo Laptop – Standard",
            "sku": "DEMO-LAP-STD",
            "vendor_sku": "DEM-L-001",
            "price": Decimal("1299.00"),
            "stock": 50,
            "description": "Demo standard laptop configuration",
        },
        {
            "name": "Demo Monitor 27\"",
            "sku": "DEMO-MON-27",
            "vendor_sku": "DEM-M-002",
            "price": Decimal("349.00"),
            "stock": 30,
            "description": "Demo 27-inch widescreen monitor",
        },
        {
            "name": "Demo Microsoft 365 Business Basic",
            "sku": "DEMO-M365-BB",
            "vendor_sku": "DEM-S-003",
            "price": Decimal("7.00"),
            "stock": 999,
            "description": "Demo Microsoft 365 Business Basic subscription seat",
        },
    ]
    for spec in product_specs:
        # Check if a demo product with this SKU already exists
        existing = await db.fetch_one(
            "SELECT id FROM shop_products WHERE sku = %s", (spec["sku"],)
        )
        if existing:
            product = await shop_repo.get_product_by_id(int(existing["id"]))
        else:
            try:
                product = await shop_repo.create_product(**spec)
            except Exception as exc:  # pragma: no cover
                log_error("Failed to create demo product", sku=spec["sku"], error=str(exc))
                continue
        if product:
            demo_products.append(product)
    stats["products"] = len(demo_products)

    # ------------------------------------------------------------------
    # 4. Orders  (requires a user linked to the company, or use admin user)
    # ------------------------------------------------------------------
    created_orders = 0
    if demo_products and seeded_by_user_id is not None:
        import random as _random
        _random.seed(42)
        order_specs = [
            (demo_products[0]["id"], 2, "Processing"),
            (demo_products[1]["id"], 4, "Delivered"),
            (demo_products[0]["id"], 1, "Pending"),
            (demo_products[1]["id"], 2, "Delivered"),
        ]
        for product_id, qty, status in order_specs:
            order_num = f"DEMO-{_random.randint(10000, 99999)}"
            try:
                await db.execute(
                    """
                    INSERT INTO shop_orders (user_id, company_id, product_id, quantity, order_number, status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (seeded_by_user_id, company_id, product_id, qty, order_num, status),
                )
                created_orders += 1
            except Exception as exc:  # pragma: no cover
                log_error("Failed to create demo order", error=str(exc))
    stats["orders"] = created_orders

    # ------------------------------------------------------------------
    # 5. Licenses
    # ------------------------------------------------------------------
    license_specs = [
        {
            "name": "Microsoft 365 Business Basic",
            "platform": "Microsoft",
            "count": 25,
            "expiry_date": datetime(today.year + 1, today.month, today.day),
            "contract_term": "Annual",
            "auto_renew": True,
        },
        {
            "name": "Adobe Creative Cloud",
            "platform": "Adobe",
            "count": 5,
            "expiry_date": datetime(today.year + 1, 1, 31),
            "contract_term": "Annual",
            "auto_renew": False,
        },
        {
            "name": "Zoom Business",
            "platform": "Zoom",
            "count": 10,
            "expiry_date": datetime(today.year, today.month, today.day) + timedelta(days=90),
            "contract_term": "Monthly",
            "auto_renew": True,
        },
        {
            "name": "Salesforce Professional",
            "platform": "Salesforce",
            "count": 5,
            "expiry_date": datetime(today.year + 1, 6, 30),
            "contract_term": "Annual",
            "auto_renew": True,
        },
        {
            "name": "Bitdefender GravityZone",
            "platform": "Bitdefender",
            "count": 30,
            "expiry_date": datetime(today.year + 1, today.month, today.day),
            "contract_term": "Annual",
            "auto_renew": True,
        },
    ]
    created_licenses = 0
    for spec in license_specs:
        try:
            await licenses_repo.create_license(company_id=company_id, **spec)
            created_licenses += 1
        except Exception as exc:  # pragma: no cover
            log_error("Failed to create demo license", name=spec["name"], error=str(exc))
    stats["licenses"] = created_licenses

    # ------------------------------------------------------------------
    # 6. Subscriptions (use the third demo product – M365 seat)
    # ------------------------------------------------------------------
    created_subscriptions = 0
    m365_product = next(
        (p for p in demo_products if "M365" in p.get("sku", "") or "M365" in p.get("name", "")),
        demo_products[2] if len(demo_products) > 2 else None,
    )
    if m365_product:
        sub_specs = [
            {
                "product_id": m365_product["id"],
                "subscription_category_id": None,
                "start_date": today - timedelta(days=30),
                "end_date": today + timedelta(days=335),
                "quantity": 25,
                "unit_price": Decimal("7.00"),
                "status": "active",
                "auto_renew": True,
            },
            {
                "product_id": demo_products[0]["id"] if demo_products else m365_product["id"],
                "subscription_category_id": None,
                "start_date": today - timedelta(days=60),
                "end_date": today + timedelta(days=305),
                "quantity": 5,
                "unit_price": Decimal("1299.00"),
                "status": "active",
                "auto_renew": False,
            },
            {
                "product_id": m365_product["id"],
                "subscription_category_id": None,
                "start_date": today - timedelta(days=90),
                "end_date": today + timedelta(days=5),
                "quantity": 3,
                "unit_price": Decimal("7.00"),
                "status": "pending_renewal",
                "auto_renew": True,
            },
        ]
        for spec in sub_specs:
            try:
                await subscriptions_repo.create_subscription(
                    customer_id=company_id,
                    created_by=seeded_by_user_id,
                    **spec,
                )
                created_subscriptions += 1
            except Exception as exc:  # pragma: no cover
                log_error("Failed to create demo subscription", error=str(exc))
    stats["subscriptions"] = created_subscriptions

    # ------------------------------------------------------------------
    # 7. Assets
    # ------------------------------------------------------------------
    asset_specs = [
        {
            "name": "DEMO-WS-001",
            "type": "Workstation",
            "serial_number": "SN-DEMO-001",
            "status": "Active",
            "os_name": "Windows 11 Pro",
            "cpu_name": "Intel Core i7-12700",
            "ram_gb": 16,
            "hdd_size": "512GB SSD",
            "last_user": "alice.anderson@demo.invalid",
            "form_factor": "Desktop",
            "warranty_status": "Under Warranty",
            "warranty_end_date": datetime(today.year + 2, 6, 30),
        },
        {
            "name": "DEMO-WS-002",
            "type": "Workstation",
            "serial_number": "SN-DEMO-002",
            "status": "Active",
            "os_name": "Windows 11 Pro",
            "cpu_name": "Intel Core i5-12400",
            "ram_gb": 8,
            "hdd_size": "256GB SSD",
            "last_user": "bob.baker@demo.invalid",
            "form_factor": "Laptop",
            "warranty_status": "Under Warranty",
            "warranty_end_date": datetime(today.year + 1, 3, 31),
        },
        {
            "name": "DEMO-SRV-001",
            "type": "Server",
            "serial_number": "SN-DEMO-SRV-001",
            "status": "Active",
            "os_name": "Windows Server 2022",
            "cpu_name": "Intel Xeon E-2334",
            "ram_gb": 32,
            "hdd_size": "2TB RAID",
            "last_user": None,
            "form_factor": "Rack",
            "warranty_status": "Under Warranty",
            "warranty_end_date": datetime(today.year + 3, 12, 31),
        },
        {
            "name": "DEMO-SRV-002",
            "type": "Server",
            "serial_number": "SN-DEMO-SRV-002",
            "status": "Active",
            "os_name": "Ubuntu Server 22.04 LTS",
            "cpu_name": "AMD EPYC 7282",
            "ram_gb": 64,
            "hdd_size": "4TB RAID",
            "last_user": None,
            "form_factor": "Rack",
            "warranty_status": "Expired",
            "warranty_end_date": datetime(today.year - 1, 6, 30),
        },
        {
            "name": "DEMO-NB-001",
            "type": "Notebook",
            "serial_number": "SN-DEMO-NB-001",
            "status": "Active",
            "os_name": "macOS Sonoma",
            "cpu_name": "Apple M2 Pro",
            "ram_gb": 16,
            "hdd_size": "512GB SSD",
            "last_user": "david.davies@demo.invalid",
            "form_factor": "Laptop",
            "warranty_status": "Under Warranty",
            "warranty_end_date": datetime(today.year + 2, 9, 30),
        },
        {
            "name": "DEMO-NB-002",
            "type": "Notebook",
            "serial_number": "SN-DEMO-NB-002",
            "status": "Retired",
            "os_name": "Windows 10 Pro",
            "cpu_name": "Intel Core i5-8250U",
            "ram_gb": 8,
            "hdd_size": "256GB SSD",
            "last_user": "frank.foster@demo.invalid",
            "form_factor": "Laptop",
            "warranty_status": "Expired",
            "warranty_end_date": datetime(today.year - 2, 1, 15),
        },
    ]
    created_assets = 0
    for spec in asset_specs:
        try:
            await assets_repo.upsert_asset(
                company_id=company_id,
                name=spec["name"],
                type=spec.get("type"),
                serial_number=spec.get("serial_number"),
                status=spec.get("status"),
                os_name=spec.get("os_name"),
                cpu_name=spec.get("cpu_name"),
                ram_gb=spec.get("ram_gb"),
                hdd_size=spec.get("hdd_size"),
                last_sync=None,
                motherboard_manufacturer=None,
                form_factor=spec.get("form_factor"),
                last_user=spec.get("last_user"),
                approx_age=None,
                performance_score=None,
                warranty_status=spec.get("warranty_status"),
                warranty_end_date=spec.get("warranty_end_date"),
                syncro_asset_id=None,
                tactical_asset_id=None,
            )
            created_assets += 1
        except Exception as exc:  # pragma: no cover
            log_error("Failed to create demo asset", name=spec["name"], error=str(exc))
    stats["assets"] = created_assets

    # ------------------------------------------------------------------
    # 8. M365 Best-practice check results (simulated)
    # ------------------------------------------------------------------
    now_utc = datetime.now(timezone.utc)
    m365_checks = [
        ("bp_mfa_all_users", "MFA enabled for all users", "pass", "All 25 users have MFA configured."),
        ("bp_admin_mfa", "MFA enabled for all admins", "pass", "All administrative accounts have MFA."),
        ("bp_legacy_auth", "Legacy authentication blocked", "fail", "Basic authentication is still enabled for legacy clients."),
        ("bp_global_admin_count", "Global admin count is acceptable", "pass", "2 Global Administrators found (recommended: 2-4)."),
        ("bp_audit_log", "Unified audit log enabled", "pass", "Audit log is enabled for all services."),
        ("bp_guest_access", "Guest access is restricted", "unknown", "Could not retrieve external sharing policy – check API permissions."),
        ("bp_secure_score", "Secure Score", "fail", "Current Secure Score: 42/100. Recommended: above 60."),
        ("bp_disable_direct_send", "Direct Send is disabled", "pass", "Direct send connectors are not configured."),
    ]
    created_m365 = 0
    for check_id, check_name, status, details in m365_checks:
        try:
            await bp_repo.upsert_result(
                company_id=company_id,
                check_id=check_id,
                check_name=check_name,
                status=status,
                details=details,
                run_at=now_utc,
            )
            created_m365 += 1
        except Exception as exc:  # pragma: no cover
            log_error("Failed to create demo M365 check", check_id=check_id, error=str(exc))
    stats["m365_checks"] = created_m365

    # ------------------------------------------------------------------
    # 9. Compliance check assignments (CUSTOM category)
    # ------------------------------------------------------------------
    created_compliance = 0
    try:
        categories = await compliance_repo.list_categories()
        custom_cat = next((c for c in categories if c["code"] == "CUSTOM"), None)
        if custom_cat:
            cat_id = custom_cat["id"]
            demo_checks = [
                {
                    "code": "DEMO-CC-001",
                    "title": "Password Policy Compliance",
                    "description": "Verify that password policy meets minimum security requirements.",
                    "guidance": "Ensure passwords are at least 12 characters, with complexity enabled.",
                    "default_review_interval_days": 180,
                    "default_evidence_required": True,
                    "is_predefined": False,
                },
                {
                    "code": "DEMO-CC-002",
                    "title": "Data Backup Verification",
                    "description": "Confirm that backup jobs are completing successfully.",
                    "guidance": "Review backup reports monthly and test restoration quarterly.",
                    "default_review_interval_days": 90,
                    "default_evidence_required": True,
                    "is_predefined": False,
                },
                {
                    "code": "DEMO-CC-003",
                    "title": "Software Asset Inventory",
                    "description": "Maintain an up-to-date register of all licensed software.",
                    "guidance": "Run automated discovery monthly and reconcile against purchase records.",
                    "default_review_interval_days": 365,
                    "default_evidence_required": False,
                    "is_predefined": False,
                },
                {
                    "code": "DEMO-CC-004",
                    "title": "Privileged Access Review",
                    "description": "Review all accounts with administrative privileges.",
                    "guidance": "Remove unnecessary admin rights; document all legitimate privileged accounts.",
                    "default_review_interval_days": 90,
                    "default_evidence_required": True,
                    "is_predefined": False,
                },
                {
                    "code": "DEMO-CC-005",
                    "title": "Incident Response Plan Review",
                    "description": "Review and update the incident response plan.",
                    "guidance": "Ensure the plan reflects current contacts, systems, and procedures.",
                    "default_review_interval_days": 365,
                    "default_evidence_required": False,
                    "is_predefined": False,
                },
            ]
            statuses = ["compliant", "non_compliant", "in_progress", "compliant", "not_started"]
            for i, check_spec in enumerate(demo_checks):
                # Upsert the check definition first (skip if code already exists)
                existing_check = await db.fetch_one(
                    "SELECT id FROM compliance_checks WHERE code = %s",
                    (check_spec["code"],),
                )
                if existing_check:
                    check_id_db = int(existing_check["id"])
                else:
                    created_check = await compliance_repo.create_check(
                        category_id=cat_id,
                        created_by=seeded_by_user_id,
                        **check_spec,
                    )
                    check_id_db = created_check["id"]

                # Skip assignment if already exists
                existing_assignment = await compliance_repo.get_assignment_by_check(
                    company_id, check_id_db, include_archived=False
                )
                if not existing_assignment:
                    from app.schemas.compliance_checks import CheckStatus
                    await compliance_repo.create_assignment(
                        company_id=company_id,
                        check_id=check_id_db,
                        status=CheckStatus(statuses[i]),
                        review_interval_days=check_spec["default_review_interval_days"],
                        owner_user_id=seeded_by_user_id,
                    )
                created_compliance += 1
    except Exception as exc:  # pragma: no cover
        log_error("Failed to create demo compliance checks", error=str(exc))
    stats["compliance_checks"] = created_compliance

    # ------------------------------------------------------------------
    # 10. Essential 8 compliance entries
    # ------------------------------------------------------------------
    created_e8 = 0
    try:
        controls = await e8_repo.list_essential8_controls()
        e8_statuses = [
            ("compliant", "ml3", "Application whitelisting enforced via WDAC."),
            ("compliant", "ml2", "Critical patches applied within 48 hours."),
            ("in_progress", "ml1", "Macro restrictions partially deployed via Group Policy."),
            ("compliant", "ml2", "Web browser hardening applied; PDF viewer configured."),
            ("in_progress", "ml1", "Admin rights under review – reduction project in progress."),
            ("non_compliant", "ml0", "OS patches delayed; EOL systems still in production."),
            ("compliant", "ml3", "MFA enforced via Conditional Access for all users."),
            ("compliant", "ml2", "Daily backups configured with 30-day retention and offline copy."),
        ]
        from app.schemas.essential8 import ComplianceStatus, MaturityLevel

        for idx, control in enumerate(controls):
            control_id = control["id"]
            existing = await e8_repo.get_company_compliance(company_id, control_id)
            if existing:
                continue
            status_val, maturity_val, notes = e8_statuses[idx % len(e8_statuses)]
            await e8_repo.create_company_compliance(
                company_id=company_id,
                control_id=control_id,
                status=ComplianceStatus(status_val),
                maturity_level=MaturityLevel(maturity_val),
                notes=notes,
                last_reviewed_date=str(today - timedelta(days=30)),
                target_compliance_date=str(today + timedelta(days=180)),
            )
            created_e8 += 1
    except Exception as exc:  # pragma: no cover
        log_error("Failed to create demo Essential 8 data", error=str(exc))
    stats["essential8"] = created_e8

    # ------------------------------------------------------------------
    # 11. Business Continuity Plan (legacy table)
    # ------------------------------------------------------------------
    created_bc = 0
    if seeded_by_user_id is not None:
        try:
            await bc_plans_repo.create_plan(
                title="Demo Business Continuity Plan",
                plan_type="business_continuity",
                content=(
                    "# Demo Business Continuity Plan\n\n"
                    "This is a sample Business Continuity Plan created by the Demo Seeder.\n\n"
                    "## Scope\nAll critical business processes for Demo Company.\n\n"
                    "## Recovery Time Objectives\n- Tier 1 systems: 4 hours RTO / 1 hour RPO\n"
                    "- Tier 2 systems: 24 hours RTO / 4 hours RPO\n\n"
                    "## Communication Plan\nContact IT Manager in the event of an outage."
                ),
                version="1.0",
                status="draft",
                created_by=seeded_by_user_id,
            )
            created_bc += 1
        except Exception as exc:  # pragma: no cover
            log_error("Failed to create demo BC plan", error=str(exc))
    stats["bc_plans"] = created_bc

    # ------------------------------------------------------------------
    # 12. Issues
    # ------------------------------------------------------------------
    created_issues = 0
    issue_specs = [
        {
            "name": "No Endpoint Detection & Response (EDR) solution deployed",
            "description": (
                "The company does not have an EDR solution deployed on all endpoints. "
                "This creates significant risk from advanced persistent threats."
            ),
            "slug": "demo-no-edr",
            "status": "open",
        },
        {
            "name": "SSL Certificate expiring within 30 days",
            "description": (
                "One or more SSL certificates are approaching expiry. "
                "Expired certificates will cause browser security warnings."
            ),
            "slug": "demo-ssl-expiry",
            "status": "open",
        },
        {
            "name": "Guest WiFi not segmented from corporate network",
            "description": (
                "The guest wireless network has access to internal resources. "
                "This should be isolated using a separate VLAN."
            ),
            "slug": "demo-wifi-segmentation",
            "status": "resolved",
        },
    ]
    for spec in issue_specs:
        try:
            # Avoid duplicates by checking slug
            existing = await db.fetch_one(
                "SELECT id FROM issue_definitions WHERE slug = %s", (spec["slug"],)
            )
            if existing:
                issue_id = int(existing["id"])
            else:
                issue = await issues_repo.create_issue(
                    name=spec["name"],
                    description=spec["description"],
                    created_by=seeded_by_user_id,
                    slug=spec["slug"],
                )
                issue_id = issue["issue_id"]

            # Assign status for this company
            await db.execute(
                """
                INSERT INTO issue_company_statuses
                    (issue_id, company_id, status, notes, updated_by)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    status = VALUES(status),
                    notes = VALUES(notes),
                    updated_by = VALUES(updated_by)
                """,
                (
                    issue_id,
                    company_id,
                    spec["status"],
                    "Demo data – example issue status.",
                    seeded_by_user_id,
                ),
            )
            created_issues += 1
        except Exception as exc:  # pragma: no cover
            log_error("Failed to create demo issue", name=spec["name"], error=str(exc))
    stats["issues"] = created_issues

    # ------------------------------------------------------------------
    # 13. Record the seed event
    # ------------------------------------------------------------------
    await db.execute(
        """
        INSERT INTO demo_seed_log (company_id, seeded_by_user_id, note)
        VALUES (%s, %s, %s)
        """,
        (company_id, seeded_by_user_id, "Initial demo seed"),
    )

    log_info("Demo data seeded successfully", **stats)
    return {"seeded": True, **stats}


async def remove_demo_data() -> dict[str, Any]:
    """
    Remove all demo company data.

    Deletes the demo company (cascades to most child records via FK) and then
    removes demo-specific shop products and issues that were created for the
    demo.

    Returns a summary dict.
    """
    company = await _get_demo_company()
    if not company:
        return {"skipped": True, "reason": "No demo company found"}

    company_id: int = int(company["id"])

    # Mark seed log entries as removed
    await db.execute(
        "UPDATE demo_seed_log SET removed_at = %s WHERE company_id = %s AND removed_at IS NULL",
        (datetime.now(timezone.utc).replace(tzinfo=None), company_id),
    )

    # Delete demo shop products (by demo SKU prefix)
    await db.execute(
        "DELETE FROM shop_products WHERE sku LIKE 'DEMO-%'",
        (),
    )

    # Delete demo issues (by demo slug prefix)
    await db.execute(
        "DELETE FROM issue_definitions WHERE slug LIKE 'demo-%'",
        (),
    )

    # Delete demo compliance checks (by demo code prefix)
    await db.execute(
        "DELETE FROM compliance_checks WHERE code LIKE 'DEMO-CC-%'",
        (),
    )

    # Delete the demo company (cascades to staff, assets, licenses, subscriptions,
    # essential8, compliance assignments, M365 results, BC plans, etc.)
    await company_repo.delete_company(company_id)

    log_info("Demo data removed", company_id=company_id)
    return {"removed": True, "company_id": company_id}
