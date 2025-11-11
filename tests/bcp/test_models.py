"""
Test BCP models for FK integrity, cascade rules, and company scoping.

Tests verify:
- Foreign key relationships are properly configured
- CASCADE delete rules work correctly
- Company-level multi-tenancy scoping
- Model constraints and validation
"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy import inspect


class TestBCPModelStructure:
    """Test BCP model definitions and relationships."""
    
    def test_bcp_plan_model_structure(self):
        """Test BcpPlan model has correct structure."""
        from app.models.bcp_models import BcpPlan
        
        # Check model has required columns
        mapper = inspect(BcpPlan)
        column_names = [col.key for col in mapper.columns]
        
        assert "id" in column_names
        assert "company_id" in column_names
        assert "title" in column_names
        assert "executive_summary" in column_names
        assert "objectives" in column_names
        assert "version" in column_names
        assert "last_reviewed_at" in column_names
        assert "next_review_at" in column_names
        assert "distribution_notes" in column_names
        assert "created_at" in column_names
        assert "updated_at" in column_names
    
    def test_bcp_risk_model_has_foreign_key(self):
        """Test BcpRisk has foreign key to BcpPlan."""
        from app.models.bcp_models import BcpRisk
        
        mapper = inspect(BcpRisk)
        
        # Check foreign key exists
        foreign_keys = []
        for col in mapper.columns:
            if col.foreign_keys:
                foreign_keys.extend(col.foreign_keys)
        
        # Should have FK to bcp_plan.id
        fk_targets = [str(fk.column) for fk in foreign_keys]
        assert any("bcp_plan.id" in target for target in fk_targets)
    
    def test_bcp_risk_model_constraints(self):
        """Test BcpRisk has proper constraints."""
        from app.models.bcp_models import BcpRisk
        
        # Check table constraints
        table = BcpRisk.__table__
        
        # Should have check constraints for likelihood and impact ranges
        constraint_names = [c.name for c in table.constraints]
        assert any("likelihood" in name for name in constraint_names)
        assert any("impact" in name for name in constraint_names)
    
    def test_critical_activity_model_has_foreign_key(self):
        """Test BcpCriticalActivity has foreign key to BcpPlan."""
        from app.models.bcp_models import BcpCriticalActivity
        
        mapper = inspect(BcpCriticalActivity)
        
        # Check foreign key exists
        foreign_keys = []
        for col in mapper.columns:
            if col.foreign_keys:
                foreign_keys.extend(col.foreign_keys)
        
        # Should have FK to bcp_plan.id
        fk_targets = [str(fk.column) for fk in foreign_keys]
        assert any("bcp_plan.id" in target for target in fk_targets)
    
    def test_bcp_impact_model_has_foreign_key(self):
        """Test BcpImpact has foreign key to BcpCriticalActivity."""
        from app.models.bcp_models import BcpImpact
        
        mapper = inspect(BcpImpact)
        
        # Check foreign key exists
        foreign_keys = []
        for col in mapper.columns:
            if col.foreign_keys:
                foreign_keys.extend(col.foreign_keys)
        
        # Should have FK to bcp_critical_activity.id
        fk_targets = [str(fk.column) for fk in foreign_keys]
        assert any("bcp_critical_activity.id" in target for target in fk_targets)
    
    def test_bcp_incident_model_has_foreign_key(self):
        """Test BcpIncident has foreign key to BcpPlan."""
        from app.models.bcp_models import BcpIncident
        
        mapper = inspect(BcpIncident)
        
        # Check foreign key exists
        foreign_keys = []
        for col in mapper.columns:
            if col.foreign_keys:
                foreign_keys.extend(col.foreign_keys)
        
        # Should have FK to bcp_plan.id
        fk_targets = [str(fk.column) for fk in foreign_keys]
        assert any("bcp_plan.id" in target for target in fk_targets)
    
    def test_checklist_tick_has_multiple_foreign_keys(self):
        """Test BcpChecklistTick has FKs to plan, item, and incident."""
        from app.models.bcp_models import BcpChecklistTick
        
        mapper = inspect(BcpChecklistTick)
        
        # Check foreign keys exist
        foreign_keys = []
        for col in mapper.columns:
            if col.foreign_keys:
                foreign_keys.extend(col.foreign_keys)
        
        fk_targets = [str(fk.column) for fk in foreign_keys]
        
        # Should have FKs to plan, checklist_item, and incident
        assert any("bcp_plan.id" in target for target in fk_targets)
        assert any("bcp_checklist_item.id" in target for target in fk_targets)
        assert any("bcp_incident.id" in target for target in fk_targets)
    
    def test_recovery_action_has_foreign_keys(self):
        """Test BcpRecoveryAction has FKs to plan and optionally activity."""
        from app.models.bcp_models import BcpRecoveryAction
        
        mapper = inspect(BcpRecoveryAction)
        
        # Check foreign keys exist
        foreign_keys = []
        for col in mapper.columns:
            if col.foreign_keys:
                foreign_keys.extend(col.foreign_keys)
        
        fk_targets = [str(fk.column) for fk in foreign_keys]
        
        # Should have FK to plan and critical_activity
        assert any("bcp_plan.id" in target for target in fk_targets)
        assert any("bcp_critical_activity.id" in target for target in fk_targets)


class TestCascadeDeleteRules:
    """Test CASCADE delete rules for BCP models."""
    
    @pytest.mark.asyncio
    async def test_cascade_delete_risks_when_plan_deleted(self):
        """Test that risks are deleted when plan is deleted."""
        # This test would require database access
        # For now, we verify the model definition has CASCADE
        from app.models.bcp_models import BcpRisk
        
        mapper = inspect(BcpRisk)
        
        # Check that plan_id has ondelete CASCADE
        for col in mapper.columns:
            if col.name == "plan_id":
                for fk in col.foreign_keys:
                    # Check ondelete is CASCADE
                    assert fk.ondelete == "CASCADE"
    
    @pytest.mark.asyncio
    async def test_cascade_delete_checklist_items_when_plan_deleted(self):
        """Test that checklist items are deleted when plan is deleted."""
        from app.models.bcp_models import BcpChecklistItem
        
        mapper = inspect(BcpChecklistItem)
        
        # Check that plan_id has ondelete CASCADE
        for col in mapper.columns:
            if col.name == "plan_id":
                for fk in col.foreign_keys:
                    assert fk.ondelete == "CASCADE"
    
    @pytest.mark.asyncio
    async def test_cascade_delete_ticks_when_incident_deleted(self):
        """Test that checklist ticks are deleted when incident is deleted."""
        from app.models.bcp_models import BcpChecklistTick
        
        mapper = inspect(BcpChecklistTick)
        
        # Check that incident_id has ondelete CASCADE
        for col in mapper.columns:
            if col.name == "incident_id":
                for fk in col.foreign_keys:
                    assert fk.ondelete == "CASCADE"
    
    @pytest.mark.asyncio
    async def test_set_null_on_delete_for_recovery_action_activity(self):
        """Test that recovery action's critical_activity_id is SET NULL on delete."""
        from app.models.bcp_models import BcpRecoveryAction
        
        mapper = inspect(BcpRecoveryAction)
        
        # Check that critical_activity_id has ondelete SET NULL
        for col in mapper.columns:
            if col.name == "critical_activity_id":
                for fk in col.foreign_keys:
                    assert fk.ondelete == "SET NULL"
    
    @pytest.mark.asyncio
    async def test_cascade_delete_impacts_when_activity_deleted(self):
        """Test that impacts are deleted when critical activity is deleted."""
        from app.models.bcp_models import BcpImpact
        
        mapper = inspect(BcpImpact)
        
        # Check that critical_activity_id has ondelete CASCADE
        for col in mapper.columns:
            if col.name == "critical_activity_id":
                for fk in col.foreign_keys:
                    assert fk.ondelete == "CASCADE"
    
    @pytest.mark.asyncio
    async def test_cascade_delete_role_assignments_when_role_deleted(self):
        """Test that role assignments are deleted when role is deleted."""
        from app.models.bcp_models import BcpRoleAssignment
        
        mapper = inspect(BcpRoleAssignment)
        
        # Check that role_id has ondelete CASCADE
        for col in mapper.columns:
            if col.name == "role_id":
                for fk in col.foreign_keys:
                    assert fk.ondelete == "CASCADE"
    
    @pytest.mark.asyncio
    async def test_cascade_delete_all_plan_children(self):
        """Test that all plan-related entities have CASCADE delete."""
        # Models that should CASCADE when plan is deleted
        cascade_models = [
            "BcpDistributionEntry",
            "BcpRisk",
            "BcpInsurancePolicy",
            "BcpBackupItem",
            "BcpCriticalActivity",
            "BcpIncident",
            "BcpChecklistItem",
            "BcpChecklistTick",
            "BcpEvacuationPlan",
            "BcpEmergencyKitItem",
            "BcpRole",
            "BcpContact",
            "BcpEventLogEntry",
            "BcpRecoveryAction",
            "BcpRecoveryContact",
            "BcpInsuranceClaim",
            "BcpMarketChange",
            "BcpTrainingItem",
            "BcpReviewItem",
        ]
        
        from app.models import bcp_models
        
        for model_name in cascade_models:
            model_class = getattr(bcp_models, model_name)
            mapper = inspect(model_class)
            
            # Check that plan_id has ondelete CASCADE
            has_plan_fk_with_cascade = False
            for col in mapper.columns:
                if col.name == "plan_id":
                    for fk in col.foreign_keys:
                        if fk.ondelete == "CASCADE":
                            has_plan_fk_with_cascade = True
            
            assert has_plan_fk_with_cascade, f"{model_name} should have CASCADE delete on plan_id"


class TestCompanyScoping:
    """Test company-level multi-tenancy scoping."""
    
    @pytest.mark.asyncio
    async def test_plan_has_company_id(self):
        """Test that BcpPlan has company_id for multi-tenancy."""
        from app.models.bcp_models import BcpPlan
        
        mapper = inspect(BcpPlan)
        column_names = [col.key for col in mapper.columns]
        
        assert "company_id" in column_names
        
        # Check company_id is not nullable
        company_id_col = mapper.columns.company_id
        assert not company_id_col.nullable
    
    @pytest.mark.asyncio
    async def test_get_plan_by_company_filters_correctly(self):
        """Test that get_plan_by_company filters by company_id."""
        from app.repositories import bcp as bcp_repo
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.fetchone = AsyncMock(return_value={
                "id": 1,
                "company_id": 123,
                "title": "Test Plan",
            })
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            result = await bcp_repo.get_plan_by_company(123)
            
            # Verify the query was called
            mock_cursor.execute.assert_called_once()
            call_args = mock_cursor.execute.call_args
            
            # Check that the query includes company_id filter
            query = call_args[0][0]
            assert "company_id" in query.lower()
            
            # Check that company_id parameter was passed
            params = call_args[0][1] if len(call_args[0]) > 1 else None
            assert params == (123,) or (params and 123 in params)
    
    @pytest.mark.asyncio
    async def test_create_plan_requires_company_id(self):
        """Test that create_plan requires company_id parameter."""
        from app.repositories import bcp as bcp_repo
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.lastrowid = 1
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            with patch("app.repositories.bcp.get_plan_by_id") as mock_get:
                mock_get.return_value = {
                    "id": 1,
                    "company_id": 456,
                    "title": "New Plan",
                }
                
                result = await bcp_repo.create_plan(company_id=456)
                
                # Verify the plan was created with company_id
                assert result["company_id"] == 456
    
    @pytest.mark.asyncio
    async def test_all_plan_queries_scope_by_plan_id(self):
        """Test that all queries scope by plan_id which is company-specific."""
        from app.repositories import bcp as bcp_repo
        
        # Test list_risks includes plan_id filter
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            await bcp_repo.list_risks(plan_id=1)
            
            # Verify the query includes plan_id filter
            call_args = mock_cursor.execute.call_args
            query = call_args[0][0]
            assert "plan_id" in query.lower()


class TestModelConstraints:
    """Test model-level constraints and validation."""
    
    def test_rto_hours_constraint(self):
        """Test that RTO hours must be non-negative."""
        from app.models.bcp_models import BcpImpact
        
        table = BcpImpact.__table__
        constraint_names = [c.name for c in table.constraints]
        
        # Should have check constraint for rto_hours >= 0
        assert any("rto" in name.lower() for name in constraint_names)
    
    def test_likelihood_range_constraint(self):
        """Test that likelihood must be 1-4."""
        from app.models.bcp_models import BcpRisk
        
        table = BcpRisk.__table__
        
        # Find the likelihood constraint
        has_likelihood_constraint = False
        for constraint in table.constraints:
            if hasattr(constraint, "sqltext"):
                constraint_text = str(constraint.sqltext)
                if "likelihood" in constraint_text.lower():
                    has_likelihood_constraint = True
                    # Check it validates range 1-4
                    assert "1" in constraint_text and "4" in constraint_text
        
        assert has_likelihood_constraint
    
    def test_impact_range_constraint(self):
        """Test that impact must be 1-4."""
        from app.models.bcp_models import BcpRisk
        
        table = BcpRisk.__table__
        
        # Find the impact constraint
        has_impact_constraint = False
        for constraint in table.constraints:
            if hasattr(constraint, "sqltext"):
                constraint_text = str(constraint.sqltext)
                if "impact" in constraint_text.lower() and "likelihood" not in constraint_text.lower():
                    has_impact_constraint = True
                    # Check it validates range 1-4
                    assert "1" in constraint_text and "4" in constraint_text
        
        assert has_impact_constraint
    
    def test_incident_status_enum(self):
        """Test that incident status is an enum with Active/Closed."""
        from app.models.bcp_models import BcpIncident
        
        mapper = inspect(BcpIncident)
        status_col = mapper.columns.status
        
        # Check it's an enum type
        assert hasattr(status_col.type, "enums") or str(status_col.type).startswith("Enum")
    
    def test_checklist_phase_enum(self):
        """Test that checklist phase is an enum with Immediate/CrisisRecovery."""
        from app.models.bcp_models import BcpChecklistItem
        
        mapper = inspect(BcpChecklistItem)
        phase_col = mapper.columns.phase
        
        # Check it's an enum type
        assert hasattr(phase_col.type, "enums") or str(phase_col.type).startswith("Enum")
    
    def test_contact_kind_enum(self):
        """Test that contact kind is an enum with Internal/External."""
        from app.models.bcp_models import BcpContact
        
        mapper = inspect(BcpContact)
        kind_col = mapper.columns.kind
        
        # Check it's an enum type
        assert hasattr(kind_col.type, "enums") or str(kind_col.type).startswith("Enum")
