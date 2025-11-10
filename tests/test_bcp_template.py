"""Tests for BCP template discovery and mapping functionality."""

import pytest

from app.schemas.bcp_template import BCPTemplateSchema, FieldType
from app.services.bcp_template import get_default_government_bcp_template


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_get_default_government_bcp_template():
    """Test that the default government BCP template is properly structured."""
    template = get_default_government_bcp_template()
    
    # Validate it's a BCPTemplateSchema
    assert isinstance(template, BCPTemplateSchema)
    
    # Check metadata
    assert template.metadata.template_name == "Government Business Continuity Plan"
    assert template.metadata.template_version == "1.0"
    assert template.metadata.requires_approval is True
    assert template.metadata.revision_tracking is True
    assert len(template.metadata.attachments_required) > 0
    
    # Check we have sections
    assert len(template.sections) > 0
    
    # Verify expected sections exist
    section_ids = [s.section_id for s in template.sections]
    expected_sections = [
        "plan_overview",
        "governance_roles",
        "business_impact_analysis",
        "risk_assessment",
        "recovery_strategies",
        "incident_response",
        "communications_plan",
        "it_systems_recovery",
        "testing_exercises",
        "maintenance_review",
        "appendices",
        "revision_history",
    ]
    
    for expected_section in expected_sections:
        assert expected_section in section_ids, f"Missing section: {expected_section}"


def test_template_section_ordering():
    """Test that template sections are properly ordered."""
    template = get_default_government_bcp_template()
    
    # Check that sections are ordered correctly
    orders = [s.order for s in template.sections]
    assert orders == sorted(orders), "Sections should be in ascending order"
    
    # Check for duplicate order values
    assert len(orders) == len(set(orders)), "No duplicate order values should exist"


def test_plan_overview_section():
    """Test the Plan Overview section structure."""
    template = get_default_government_bcp_template()
    
    plan_overview = next((s for s in template.sections if s.section_id == "plan_overview"), None)
    assert plan_overview is not None
    assert plan_overview.title == "Plan Overview"
    assert plan_overview.order == 1
    
    # Check fields
    field_ids = [f.field_id for f in plan_overview.fields]
    assert "purpose" in field_ids
    assert "scope" in field_ids
    assert "objectives" in field_ids
    assert "assumptions" in field_ids
    
    # Check purpose field is rich_text and required
    purpose_field = next(f for f in plan_overview.fields if f.field_id == "purpose")
    assert purpose_field.field_type == FieldType.RICH_TEXT
    assert purpose_field.required is True


def test_business_impact_analysis_section():
    """Test the Business Impact Analysis section with tables."""
    template = get_default_government_bcp_template()
    
    bia_section = next((s for s in template.sections if s.section_id == "business_impact_analysis"), None)
    assert bia_section is not None
    
    # Find critical_processes field (should be a table)
    critical_processes = next(
        (f for f in bia_section.fields if f.field_id == "critical_processes"), None
    )
    assert critical_processes is not None
    assert critical_processes.field_type == FieldType.TABLE
    assert critical_processes.columns is not None
    assert len(critical_processes.columns) > 0
    
    # Check for RTO, RPO, MTPD columns
    column_ids = [c.column_id for c in critical_processes.columns]
    assert "rto" in column_ids
    assert "rpo" in column_ids
    assert "mtpd" in column_ids
    
    # Check for impact category columns
    assert "financial_impact" in column_ids
    assert "operational_impact" in column_ids
    assert "legal_impact" in column_ids
    assert "reputation_impact" in column_ids


def test_risk_assessment_section():
    """Test the Risk Assessment section structure."""
    template = get_default_government_bcp_template()
    
    risk_section = next((s for s in template.sections if s.section_id == "risk_assessment"), None)
    assert risk_section is not None
    
    # Find identified_risks table
    risks_table = next((f for f in risk_section.fields if f.field_id == "identified_risks"), None)
    assert risks_table is not None
    assert risks_table.field_type == FieldType.TABLE
    
    # Check for key risk assessment columns
    column_ids = [c.column_id for c in risks_table.columns]
    assert "threat_name" in column_ids
    assert "likelihood" in column_ids
    assert "impact" in column_ids
    assert "risk_rating" in column_ids
    assert "mitigation_strategies" in column_ids
    
    # Check that risk_rating has is_computed flag
    risk_rating_col = next(c for c in risks_table.columns if c.column_id == "risk_rating")
    assert risk_rating_col.is_computed is True


def test_incident_response_section():
    """Test the Incident Response section structure."""
    template = get_default_government_bcp_template()
    
    ir_section = next((s for s in template.sections if s.section_id == "incident_response"), None)
    assert ir_section is not None
    
    # Check for notification tree
    notification_tree = next((f for f in ir_section.fields if f.field_id == "notification_tree"), None)
    assert notification_tree is not None
    assert notification_tree.field_type == FieldType.TABLE
    
    # Check columns
    column_ids = [c.column_id for c in notification_tree.columns]
    assert "notification_order" in column_ids
    assert "role_title" in column_ids
    assert "contact" in column_ids
    assert "notification_method" in column_ids
    
    # Check that notification_method is multiselect
    notification_method_col = next(
        c for c in notification_tree.columns if c.column_id == "notification_method"
    )
    assert notification_method_col.field_type == FieldType.MULTISELECT
    assert notification_method_col.choices is not None
    assert len(notification_method_col.choices) > 0


def test_it_systems_recovery_section():
    """Test the IT/Systems Recovery section structure."""
    template = get_default_government_bcp_template()
    
    it_section = next((s for s in template.sections if s.section_id == "it_systems_recovery"), None)
    assert it_section is not None
    
    # Check for critical applications table
    apps_table = next((f for f in it_section.fields if f.field_id == "critical_applications"), None)
    assert apps_table is not None
    assert apps_table.field_type == FieldType.TABLE
    
    # Check for recovery_runbook URL column
    column_ids = [c.column_id for c in apps_table.columns]
    assert "recovery_runbook" in column_ids
    
    runbook_col = next(c for c in apps_table.columns if c.column_id == "recovery_runbook")
    assert runbook_col.field_type == FieldType.URL
    
    # Check for test_cadence field
    test_cadence = next((f for f in it_section.fields if f.field_id == "test_cadence"), None)
    assert test_cadence is not None
    assert test_cadence.field_type == FieldType.SELECT
    assert test_cadence.choices is not None


def test_appendices_section():
    """Test the Appendices section with file fields."""
    template = get_default_government_bcp_template()
    
    appendices = next((s for s in template.sections if s.section_id == "appendices"), None)
    assert appendices is not None
    
    # Check for file fields
    contact_list = next((f for f in appendices.fields if f.field_id == "contact_list"), None)
    assert contact_list is not None
    assert contact_list.field_type == FieldType.FILE
    assert contact_list.required is True
    
    vendor_slas = next((f for f in appendices.fields if f.field_id == "vendor_slas"), None)
    assert vendor_slas is not None
    assert vendor_slas.field_type == FieldType.FILE


def test_revision_history_section():
    """Test the Revision History section with computed table."""
    template = get_default_government_bcp_template()
    
    revision_section = next((s for s in template.sections if s.section_id == "revision_history"), None)
    assert revision_section is not None
    
    # Check for revision history table
    revision_table = next(
        (f for f in revision_section.fields if f.field_id == "revision_history_table"), None
    )
    assert revision_table is not None
    assert revision_table.field_type == FieldType.TABLE
    assert revision_table.is_computed is True
    assert revision_table.computation_note is not None
    
    # Check columns
    column_ids = [c.column_id for c in revision_table.columns]
    assert "version" in column_ids
    assert "author" in column_ids
    assert "date" in column_ids
    assert "summary" in column_ids


def test_field_types_coverage():
    """Test that the template uses a variety of field types."""
    template = get_default_government_bcp_template()
    
    # Collect all field types used
    field_types = set()
    for section in template.sections:
        for field in section.fields:
            field_types.add(field.field_type)
            # Also collect column field types from tables
            if field.columns:
                for column in field.columns:
                    field_types.add(column.field_type)
    
    # Check that we're using a good variety of field types
    expected_types = {
        FieldType.TEXT,
        FieldType.RICH_TEXT,
        FieldType.SELECT,
        FieldType.MULTISELECT,
        FieldType.TABLE,
        FieldType.FILE,
        FieldType.USER_REF,
        FieldType.CONTACT_REF,
        FieldType.URL,
        FieldType.DATE,
        FieldType.INTEGER,
    }
    
    for expected_type in expected_types:
        assert expected_type in field_types, f"Field type {expected_type} should be used in template"


def test_required_fields():
    """Test that critical fields are marked as required."""
    template = get_default_government_bcp_template()
    
    # Plan Overview - purpose and scope should be required
    plan_overview = next(s for s in template.sections if s.section_id == "plan_overview")
    purpose = next(f for f in plan_overview.fields if f.field_id == "purpose")
    assert purpose.required is True
    
    scope = next(f for f in plan_overview.fields if f.field_id == "scope")
    assert scope.required is True
    
    # BIA - critical processes should be required
    bia = next(s for s in template.sections if s.section_id == "business_impact_analysis")
    critical_processes = next(f for f in bia.fields if f.field_id == "critical_processes")
    assert critical_processes.required is True


def test_select_field_choices():
    """Test that select fields have proper choices defined."""
    template = get_default_government_bcp_template()
    
    # Find a select field and verify it has choices
    it_section = next(s for s in template.sections if s.section_id == "it_systems_recovery")
    test_cadence = next(f for f in it_section.fields if f.field_id == "test_cadence")
    
    assert test_cadence.field_type == FieldType.SELECT
    assert test_cadence.choices is not None
    assert len(test_cadence.choices) > 0
    
    # Check that choices have value and label
    for choice in test_cadence.choices:
        assert choice.value
        assert choice.label


def test_computed_fields():
    """Test that computed fields are properly marked."""
    template = get_default_government_bcp_template()
    
    # Check BIA dependencies table
    bia = next(s for s in template.sections if s.section_id == "business_impact_analysis")
    dependencies = next(f for f in bia.fields if f.field_id == "process_dependencies")
    assert dependencies.is_computed is True
    assert dependencies.computation_note is not None
    
    # Check revision history table
    revision_section = next(s for s in template.sections if s.section_id == "revision_history")
    revision_table = next(f for f in revision_section.fields if f.field_id == "revision_history_table")
    assert revision_table.is_computed is True


def test_help_text_present():
    """Test that fields have helpful help_text."""
    template = get_default_government_bcp_template()
    
    # Check that most fields have help text
    fields_with_help_text = 0
    total_fields = 0
    
    for section in template.sections:
        for field in section.fields:
            total_fields += 1
            if field.help_text:
                fields_with_help_text += 1
    
    # At least 80% of fields should have help text
    help_text_ratio = fields_with_help_text / total_fields
    assert help_text_ratio >= 0.8, f"Only {help_text_ratio:.1%} of fields have help text"
