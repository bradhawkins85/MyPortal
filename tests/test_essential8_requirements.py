"""Tests for Essential 8 requirements tracking."""
import pytest
from app.core.database import db
from app.repositories import essential8 as essential8_repo
from app.schemas.essential8 import ComplianceStatus


@pytest.mark.asyncio
async def test_list_essential8_requirements():
    """Test listing Essential 8 requirements."""
    await db.connect()
    try:
        # Get all requirements
        requirements = await essential8_repo.list_essential8_requirements()
        
        # Should have many requirements (100+)
        assert len(requirements) > 50
        
        # Check structure
        assert "id" in requirements[0]
        assert "control_id" in requirements[0]
        assert "maturity_level" in requirements[0]
        assert "requirement_order" in requirements[0]
        assert "description" in requirements[0]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_list_requirements_by_control():
    """Test filtering requirements by control."""
    await db.connect()
    try:
        # Get all controls
        controls = await essential8_repo.list_essential8_controls()
        control_id = controls[0]["id"]
        
        # Get requirements for first control
        requirements = await essential8_repo.list_essential8_requirements(
            control_id=control_id
        )
        
        # All requirements should belong to this control
        for req in requirements:
            assert req["control_id"] == control_id
        
        # Should have requirements for multiple maturity levels
        ml_levels = set(req["maturity_level"] for req in requirements)
        assert len(ml_levels) > 0
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_list_requirements_by_maturity_level():
    """Test filtering requirements by maturity level."""
    await db.connect()
    try:
        # Get ML1 requirements
        ml1_requirements = await essential8_repo.list_essential8_requirements(
            maturity_level="ml1"
        )
        
        # All should be ML1
        for req in ml1_requirements:
            assert req["maturity_level"] == "ml1"
        
        # Should have some ML1 requirements
        assert len(ml1_requirements) > 0
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_get_control_with_requirements():
    """Test getting a control with all its requirements."""
    await db.connect()
    try:
        # Get all controls
        controls = await essential8_repo.list_essential8_controls()
        control_id = controls[0]["id"]
        
        # Get control with requirements
        control_data = await essential8_repo.get_control_with_requirements(
            control_id=control_id
        )
        
        assert control_data is not None
        assert "control" in control_data
        assert "requirements_ml1" in control_data
        assert "requirements_ml2" in control_data
        assert "requirements_ml3" in control_data
        
        # Should have requirements
        total_reqs = (
            len(control_data["requirements_ml1"]) +
            len(control_data["requirements_ml2"]) +
            len(control_data["requirements_ml3"])
        )
        assert total_reqs > 0
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_initialize_company_requirement_compliance():
    """Test initializing requirement compliance for a company."""
    await db.connect()
    try:
        # Create a test company
        company_query = "INSERT INTO companies (name) VALUES (%(name)s)"
        company_id = await db.execute(company_query, {"name": "Test Company Requirements"})
        
        # Initialize requirement compliance
        created_count = await essential8_repo.initialize_company_requirement_compliance(
            company_id=company_id
        )
        
        # Should create many records (100+)
        assert created_count > 50
        
        # Verify records were created
        records = await essential8_repo.list_company_requirement_compliance(
            company_id=company_id
        )
        assert len(records) == created_count
        
        # All should have default status
        for record in records:
            assert record["status"] == ComplianceStatus.NOT_STARTED.value
        
        # Cleanup
        await db.execute(
            "DELETE FROM company_essential8_requirement_compliance WHERE company_id = %(company_id)s",
            {"company_id": company_id}
        )
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_create_and_update_requirement_compliance():
    """Test creating and updating requirement compliance."""
    await db.connect()
    try:
        # Create a test company
        company_query = "INSERT INTO companies (name) VALUES (%(name)s)"
        company_id = await db.execute(company_query, {"name": "Test Company Req Update"})
        
        # Get a requirement
        requirements = await essential8_repo.list_essential8_requirements()
        requirement_id = requirements[0]["id"]
        
        # Create compliance record
        record = await essential8_repo.create_company_requirement_compliance(
            company_id=company_id,
            requirement_id=requirement_id,
            status=ComplianceStatus.IN_PROGRESS,
            notes="Working on this",
        )
        
        assert record["company_id"] == company_id
        assert record["requirement_id"] == requirement_id
        assert record["status"] == ComplianceStatus.IN_PROGRESS.value
        assert record["notes"] == "Working on this"
        
        # Update the record
        updated = await essential8_repo.update_company_requirement_compliance(
            company_id=company_id,
            requirement_id=requirement_id,
            status=ComplianceStatus.COMPLIANT,
            evidence="Test evidence",
        )
        
        assert updated is not None
        assert updated["status"] == ComplianceStatus.COMPLIANT.value
        assert updated["evidence"] == "Test evidence"
        
        # Cleanup
        await db.execute(
            "DELETE FROM company_essential8_requirement_compliance WHERE company_id = %(company_id)s",
            {"company_id": company_id}
        )
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_calculate_control_compliance_from_requirements():
    """Test calculating control compliance based on requirements."""
    await db.connect()
    try:
        # Create a test company
        company_query = "INSERT INTO companies (name) VALUES (%(name)s)"
        company_id = await db.execute(company_query, {"name": "Test Company Calc"})
        
        # Get first control and its requirements
        controls = await essential8_repo.list_essential8_controls()
        control_id = controls[0]["id"]
        requirements = await essential8_repo.list_essential8_requirements(
            control_id=control_id
        )
        
        # Initialize requirement compliance
        await essential8_repo.initialize_company_requirement_compliance(
            company_id=company_id,
            control_id=control_id,
        )
        
        # Mark all requirements as compliant or not applicable
        for req in requirements[:len(requirements)//2]:
            await essential8_repo.update_company_requirement_compliance(
                company_id=company_id,
                requirement_id=req["id"],
                status=ComplianceStatus.COMPLIANT,
            )
        
        for req in requirements[len(requirements)//2:]:
            await essential8_repo.update_company_requirement_compliance(
                company_id=company_id,
                requirement_id=req["id"],
                status=ComplianceStatus.NOT_APPLICABLE,
            )
        
        # Calculate compliance
        compliance_calc = await essential8_repo.calculate_control_compliance_from_requirements(
            company_id=company_id,
            control_id=control_id,
        )
        
        assert compliance_calc["is_compliant"] is True
        assert compliance_calc["total_requirements"] == len(requirements)
        assert compliance_calc["compliant_count"] == len(requirements) // 2
        assert compliance_calc["not_applicable_count"] == len(requirements) - len(requirements) // 2
        
        # Cleanup
        await db.execute(
            "DELETE FROM company_essential8_requirement_compliance WHERE company_id = %(company_id)s",
            {"company_id": company_id}
        )
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_auto_update_control_compliance():
    """Test auto-updating control compliance based on requirements."""
    await db.connect()
    try:
        # Create a test company
        company_query = "INSERT INTO companies (name) VALUES (%(name)s)"
        company_id = await db.execute(company_query, {"name": "Test Company Auto"})
        
        # Get first control and its requirements
        controls = await essential8_repo.list_essential8_controls()
        control_id = controls[0]["id"]
        
        # Initialize both control and requirement compliance
        await essential8_repo.initialize_company_compliance(company_id)
        await essential8_repo.initialize_company_requirement_compliance(
            company_id=company_id,
            control_id=control_id,
        )
        
        # Get requirements
        requirements = await essential8_repo.list_essential8_requirements(
            control_id=control_id
        )
        
        # Mark all requirements as compliant
        for req in requirements:
            await essential8_repo.update_company_requirement_compliance(
                company_id=company_id,
                requirement_id=req["id"],
                status=ComplianceStatus.COMPLIANT,
            )
        
        # Auto-update control compliance
        updated_control = await essential8_repo.auto_update_control_compliance_from_requirements(
            company_id=company_id,
            control_id=control_id,
        )
        
        # Control should now be compliant
        assert updated_control is not None
        assert updated_control["status"] == ComplianceStatus.COMPLIANT.value
        
        # Cleanup
        await db.execute(
            "DELETE FROM company_essential8_requirement_compliance WHERE company_id = %(company_id)s",
            {"company_id": company_id}
        )
        await db.execute(
            "DELETE FROM company_essential8_compliance WHERE company_id = %(company_id)s",
            {"company_id": company_id}
        )
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
    finally:
        await db.disconnect()
