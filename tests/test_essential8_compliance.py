"""Tests for Essential 8 compliance tracking."""
import pytest
from app.core.database import db
from app.repositories import essential8 as essential8_repo
from app.schemas.essential8 import ComplianceStatus, MaturityLevel


@pytest.mark.asyncio
async def test_list_essential8_controls():
    """Test listing all Essential 8 controls."""
    await db.connect()
    try:
        controls = await essential8_repo.list_essential8_controls()
        
        # Should have exactly 8 controls
        assert len(controls) == 8
        
        # Check first control
        assert controls[0]["name"] == "Application Control"
        assert controls[0]["control_order"] == 1
        
        # Check last control
        assert controls[7]["name"] == "Regular Backups"
        assert controls[7]["control_order"] == 8
        
        # Verify all controls have required fields
        for control in controls:
            assert "id" in control
            assert "name" in control
            assert "description" in control
            assert "control_order" in control
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_get_essential8_control():
    """Test getting a specific Essential 8 control."""
    await db.connect()
    try:
        # Get all controls first to get a valid ID
        controls = await essential8_repo.list_essential8_controls()
        control_id = controls[0]["id"]
        
        # Get the specific control
        control = await essential8_repo.get_essential8_control(control_id)
        
        assert control is not None
        assert control["id"] == control_id
        assert control["name"] == "Application Control"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_initialize_company_compliance():
    """Test initializing compliance records for a company."""
    await db.connect()
    try:
        # Create a test company
        company_query = "INSERT INTO companies (name) VALUES (%(name)s)"
        company_id = await db.execute(company_query, {"name": "Test Company"})
        
        # Initialize compliance records
        created_count = await essential8_repo.initialize_company_compliance(company_id)
        
        # Should create 8 records
        assert created_count == 8
        
        # Verify records were created
        records = await essential8_repo.list_company_compliance(company_id)
        assert len(records) == 8
        
        # All should have default status
        for record in records:
            assert record["status"] == ComplianceStatus.NOT_STARTED.value
            assert record["maturity_level"] == MaturityLevel.ML0.value
        
        # Cleanup
        await db.execute("DELETE FROM company_essential8_compliance WHERE company_id = %(company_id)s", {"company_id": company_id})
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_initialize_company_compliance_idempotent():
    """Test that initializing twice doesn't create duplicates."""
    await db.connect()
    try:
        # Create a test company
        company_query = "INSERT INTO companies (name) VALUES (%(name)s)"
        company_id = await db.execute(company_query, {"name": "Test Company 2"})
        
        # Initialize compliance records twice
        created_count_1 = await essential8_repo.initialize_company_compliance(company_id)
        created_count_2 = await essential8_repo.initialize_company_compliance(company_id)
        
        # First should create 8, second should create 0
        assert created_count_1 == 8
        assert created_count_2 == 0
        
        # Should still only have 8 records
        records = await essential8_repo.list_company_compliance(company_id)
        assert len(records) == 8
        
        # Cleanup
        await db.execute("DELETE FROM company_essential8_compliance WHERE company_id = %(company_id)s", {"company_id": company_id})
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_create_and_get_company_compliance():
    """Test creating and retrieving a compliance record."""
    await db.connect()
    try:
        # Create a test company
        company_query = "INSERT INTO companies (name) VALUES (%(name)s)"
        company_id = await db.execute(company_query, {"name": "Test Company 3"})
        
        # Get a control ID
        controls = await essential8_repo.list_essential8_controls()
        control_id = controls[0]["id"]
        
        # Create a compliance record
        record = await essential8_repo.create_company_compliance(
            company_id=company_id,
            control_id=control_id,
            status=ComplianceStatus.IN_PROGRESS,
            maturity_level=MaturityLevel.ML1,
            notes="Working on this",
        )
        
        assert record["company_id"] == company_id
        assert record["control_id"] == control_id
        assert record["status"] == ComplianceStatus.IN_PROGRESS.value
        assert record["maturity_level"] == MaturityLevel.ML1.value
        assert record["notes"] == "Working on this"
        
        # Get the record
        retrieved = await essential8_repo.get_company_compliance(company_id, control_id)
        
        assert retrieved is not None
        assert retrieved["id"] == record["id"]
        assert retrieved["status"] == ComplianceStatus.IN_PROGRESS.value
        
        # Cleanup
        await db.execute("DELETE FROM company_essential8_compliance WHERE company_id = %(company_id)s", {"company_id": company_id})
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_update_company_compliance():
    """Test updating a compliance record."""
    await db.connect()
    try:
        # Create a test company
        company_query = "INSERT INTO companies (name) VALUES (%(name)s)"
        company_id = await db.execute(company_query, {"name": "Test Company 4"})
        
        # Get a control ID
        controls = await essential8_repo.list_essential8_controls()
        control_id = controls[0]["id"]
        
        # Create a compliance record
        record = await essential8_repo.create_company_compliance(
            company_id=company_id,
            control_id=control_id,
            status=ComplianceStatus.NOT_STARTED,
            maturity_level=MaturityLevel.ML0,
        )
        
        # Update the record
        updated = await essential8_repo.update_company_compliance(
            company_id=company_id,
            control_id=control_id,
            user_id=None,
            status=ComplianceStatus.COMPLIANT,
            maturity_level=MaturityLevel.ML2,
            notes="Now compliant",
        )
        
        assert updated is not None
        assert updated["status"] == ComplianceStatus.COMPLIANT.value
        assert updated["maturity_level"] == MaturityLevel.ML2.value
        assert updated["notes"] == "Now compliant"
        
        # Cleanup
        await db.execute("DELETE FROM company_essential8_audit WHERE company_id = %(company_id)s", {"company_id": company_id})
        await db.execute("DELETE FROM company_essential8_compliance WHERE company_id = %(company_id)s", {"company_id": company_id})
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_get_company_compliance_summary():
    """Test getting compliance summary for a company."""
    await db.connect()
    try:
        # Create a test company
        company_query = "INSERT INTO companies (name) VALUES (%(name)s)"
        company_id = await db.execute(company_query, {"name": "Test Company 5"})
        
        # Initialize compliance records
        await essential8_repo.initialize_company_compliance(company_id)
        
        # Get all controls
        controls = await essential8_repo.list_essential8_controls()
        
        # Update some records to different statuses
        await essential8_repo.update_company_compliance(
            company_id=company_id,
            control_id=controls[0]["id"],
            status=ComplianceStatus.COMPLIANT,
            maturity_level=MaturityLevel.ML3,
        )
        await essential8_repo.update_company_compliance(
            company_id=company_id,
            control_id=controls[1]["id"],
            status=ComplianceStatus.COMPLIANT,
            maturity_level=MaturityLevel.ML2,
        )
        await essential8_repo.update_company_compliance(
            company_id=company_id,
            control_id=controls[2]["id"],
            status=ComplianceStatus.IN_PROGRESS,
            maturity_level=MaturityLevel.ML1,
        )
        
        # Get summary
        summary = await essential8_repo.get_company_compliance_summary(company_id)
        
        assert summary["company_id"] == company_id
        assert summary["total_controls"] == 8
        assert summary["compliant"] == 2
        assert summary["in_progress"] == 1
        assert summary["not_started"] == 5
        assert summary["compliance_percentage"] == 25.0  # 2/8 = 25%
        
        # Cleanup
        await db.execute("DELETE FROM company_essential8_audit WHERE company_id = %(company_id)s", {"company_id": company_id})
        await db.execute("DELETE FROM company_essential8_compliance WHERE company_id = %(company_id)s", {"company_id": company_id})
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_list_company_compliance_with_filter():
    """Test listing compliance records with status filter."""
    await db.connect()
    try:
        # Create a test company
        company_query = "INSERT INTO companies (name) VALUES (%(name)s)"
        company_id = await db.execute(company_query, {"name": "Test Company 6"})
        
        # Initialize compliance records
        await essential8_repo.initialize_company_compliance(company_id)
        
        # Get all controls
        controls = await essential8_repo.list_essential8_controls()
        
        # Update some to compliant
        await essential8_repo.update_company_compliance(
            company_id=company_id,
            control_id=controls[0]["id"],
            status=ComplianceStatus.COMPLIANT,
        )
        await essential8_repo.update_company_compliance(
            company_id=company_id,
            control_id=controls[1]["id"],
            status=ComplianceStatus.COMPLIANT,
        )
        
        # List all records
        all_records = await essential8_repo.list_company_compliance(company_id)
        assert len(all_records) == 8
        
        # List only compliant
        compliant_records = await essential8_repo.list_company_compliance(
            company_id,
            status=ComplianceStatus.COMPLIANT,
        )
        assert len(compliant_records) == 2
        
        # List only not started
        not_started_records = await essential8_repo.list_company_compliance(
            company_id,
            status=ComplianceStatus.NOT_STARTED,
        )
        assert len(not_started_records) == 6
        
        # Cleanup
        await db.execute("DELETE FROM company_essential8_audit WHERE company_id = %(company_id)s", {"company_id": company_id})
        await db.execute("DELETE FROM company_essential8_compliance WHERE company_id = %(company_id)s", {"company_id": company_id})
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_compliance_audit_trail():
    """Test that audit trail is created on updates."""
    await db.connect()
    try:
        # Create a test company and user
        company_query = "INSERT INTO companies (name) VALUES (%(name)s)"
        company_id = await db.execute(company_query, {"name": "Test Company 7"})
        
        user_query = "INSERT INTO users (email, password_hash, company_id) VALUES (%(email)s, %(password_hash)s, %(company_id)s)"
        user_id = await db.execute(user_query, {
            "email": "test@example.com",
            "password_hash": "hash",
            "company_id": company_id,
        })
        
        # Get a control ID
        controls = await essential8_repo.list_essential8_controls()
        control_id = controls[0]["id"]
        
        # Create a compliance record
        record = await essential8_repo.create_company_compliance(
            company_id=company_id,
            control_id=control_id,
            status=ComplianceStatus.NOT_STARTED,
            maturity_level=MaturityLevel.ML0,
        )
        
        # Update the record (this should create an audit entry)
        await essential8_repo.update_company_compliance(
            company_id=company_id,
            control_id=control_id,
            user_id=user_id,
            status=ComplianceStatus.COMPLIANT,
            maturity_level=MaturityLevel.ML2,
            notes="Completed implementation",
        )
        
        # Get audit trail
        audit = await essential8_repo.list_compliance_audit(
            company_id=company_id,
            control_id=control_id,
        )
        
        assert len(audit) > 0
        audit_entry = audit[0]
        assert audit_entry["action"] == "update"
        assert audit_entry["user_id"] == user_id
        assert audit_entry["old_status"] == ComplianceStatus.NOT_STARTED.value
        assert audit_entry["new_status"] == ComplianceStatus.COMPLIANT.value
        
        # Cleanup
        await db.execute("DELETE FROM company_essential8_audit WHERE company_id = %(company_id)s", {"company_id": company_id})
        await db.execute("DELETE FROM company_essential8_compliance WHERE company_id = %(company_id)s", {"company_id": company_id})
        await db.execute("DELETE FROM users WHERE id = %(id)s", {"id": user_id})
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
    finally:
        await db.disconnect()
