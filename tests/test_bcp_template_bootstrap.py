"""Tests for BCP template bootstrap functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.bcp_template import bootstrap_default_template, get_default_government_bcp_template


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.asyncio
async def test_bootstrap_default_template_creates_new():
    """Test that bootstrap creates a new template when none exists."""
    # Mock the repository functions
    with patch('app.repositories.bc3') as mock_repo:
        # No existing template
        mock_repo.get_default_template = AsyncMock(return_value=None)
        
        # Mock the create_template function
        mock_template = {
            'id': 1,
            'name': 'Government Business Continuity Plan',
            'version': '1.0',
            'is_default': True,
            'schema_json': {'metadata': {}, 'sections': []},
            'created_at': '2024-01-01T00:00:00',
            'updated_at': '2024-01-01T00:00:00',
        }
        mock_repo.create_template = AsyncMock(return_value=mock_template)
        
        # Call bootstrap
        result = await bootstrap_default_template()
        
        # Verify create_template was called
        assert mock_repo.create_template.called
        call_kwargs = mock_repo.create_template.call_args[1]
        assert call_kwargs['name'] == 'Government Business Continuity Plan'
        assert call_kwargs['version'] == '1.0'
        assert call_kwargs['is_default'] is True
        assert call_kwargs['schema_json'] is not None
        
        # Verify result
        assert result == mock_template


@pytest.mark.asyncio
async def test_bootstrap_default_template_returns_existing():
    """Test that bootstrap returns existing template if one already exists."""
    # Mock the repository functions
    with patch('app.repositories.bc3') as mock_repo:
        # Existing template
        existing_template = {
            'id': 1,
            'name': 'Government Business Continuity Plan',
            'version': '1.0',
            'is_default': True,
            'schema_json': {'metadata': {}, 'sections': []},
            'created_at': '2024-01-01T00:00:00',
            'updated_at': '2024-01-01T00:00:00',
        }
        mock_repo.get_default_template = AsyncMock(return_value=existing_template)
        mock_repo.create_template = AsyncMock()
        
        # Call bootstrap
        result = await bootstrap_default_template()
        
        # Verify create_template was NOT called
        assert not mock_repo.create_template.called
        
        # Verify result is the existing template
        assert result == existing_template


@pytest.mark.asyncio
async def test_bootstrap_template_schema_structure():
    """Test that bootstrapped template has correct schema structure."""
    with patch('app.repositories.bc3') as mock_repo:
        mock_repo.get_default_template = AsyncMock(return_value=None)
        
        # Capture the schema_json passed to create_template
        captured_schema = None
        
        async def capture_create_template(*args, **kwargs):
            nonlocal captured_schema
            captured_schema = kwargs.get('schema_json')
            return {
                'id': 1,
                'name': kwargs['name'],
                'version': kwargs['version'],
                'is_default': kwargs['is_default'],
                'schema_json': captured_schema,
            }
        
        mock_repo.create_template = AsyncMock(side_effect=capture_create_template)
        
        # Call bootstrap
        await bootstrap_default_template()
        
        # Verify schema structure
        assert captured_schema is not None
        assert 'metadata' in captured_schema
        assert 'sections' in captured_schema
        
        # Check metadata
        metadata = captured_schema['metadata']
        assert metadata['template_name'] == 'Government Business Continuity Plan'
        assert metadata['template_version'] == '1.0'
        assert metadata['requires_approval'] is True
        assert metadata['revision_tracking'] is True
        
        # Check sections
        sections = captured_schema['sections']
        assert len(sections) > 0
        
        # Verify sections have proper structure
        for section in sections:
            assert 'section_id' in section
            assert 'title' in section
            assert 'order' in section
            assert 'fields' in section
            
            # Verify fields have proper structure
            for field in section['fields']:
                assert 'field_id' in field
                assert 'label' in field
                assert 'field_type' in field
                assert 'required' in field


@pytest.mark.asyncio
async def test_bootstrap_includes_bia_table_schema():
    """Test that bootstrap includes BIA table with correct columns."""
    with patch('app.repositories.bc3') as mock_repo:
        mock_repo.get_default_template = AsyncMock(return_value=None)
        
        captured_schema = None
        
        async def capture_create_template(*args, **kwargs):
            nonlocal captured_schema
            captured_schema = kwargs.get('schema_json')
            return {'id': 1, 'schema_json': captured_schema}
        
        mock_repo.create_template = AsyncMock(side_effect=capture_create_template)
        
        await bootstrap_default_template()
        
        # Find BIA section
        bia_section = next(
            (s for s in captured_schema['sections'] if s['section_id'] == 'business_impact_analysis'),
            None
        )
        assert bia_section is not None, "BIA section should exist"
        
        # Find critical_processes table
        critical_processes = next(
            (f for f in bia_section['fields'] if f['field_id'] == 'critical_processes'),
            None
        )
        assert critical_processes is not None
        assert critical_processes['field_type'] == 'table'
        assert 'columns' in critical_processes
        
        # Verify RTO, RPO, MTPD columns exist
        column_ids = [c['column_id'] for c in critical_processes['columns']]
        assert 'rto' in column_ids
        assert 'rpo' in column_ids
        assert 'mtpd' in column_ids


@pytest.mark.asyncio
async def test_bootstrap_includes_risk_table_schema():
    """Test that bootstrap includes Risk assessment table."""
    with patch('app.repositories.bc3') as mock_repo:
        mock_repo.get_default_template = AsyncMock(return_value=None)
        
        captured_schema = None
        
        async def capture_create_template(*args, **kwargs):
            nonlocal captured_schema
            captured_schema = kwargs.get('schema_json')
            return {'id': 1, 'schema_json': captured_schema}
        
        mock_repo.create_template = AsyncMock(side_effect=capture_create_template)
        
        await bootstrap_default_template()
        
        # Find Risk Assessment section
        risk_section = next(
            (s for s in captured_schema['sections'] if s['section_id'] == 'risk_assessment'),
            None
        )
        assert risk_section is not None, "Risk Assessment section should exist"
        
        # Find identified_risks table
        risks_table = next(
            (f for f in risk_section['fields'] if f['field_id'] == 'identified_risks'),
            None
        )
        assert risks_table is not None
        assert risks_table['field_type'] == 'table'
        assert 'columns' in risks_table
        
        # Verify key columns exist
        column_ids = [c['column_id'] for c in risks_table['columns']]
        assert 'threat_name' in column_ids
        assert 'likelihood' in column_ids
        assert 'impact' in column_ids
        assert 'risk_rating' in column_ids
        assert 'mitigation_strategies' in column_ids


@pytest.mark.asyncio
async def test_bootstrap_includes_contact_table_schema():
    """Test that bootstrap includes Contact list in appendices."""
    with patch('app.repositories.bc3') as mock_repo:
        mock_repo.get_default_template = AsyncMock(return_value=None)
        
        captured_schema = None
        
        async def capture_create_template(*args, **kwargs):
            nonlocal captured_schema
            captured_schema = kwargs.get('schema_json')
            return {'id': 1, 'schema_json': captured_schema}
        
        mock_repo.create_template = AsyncMock(side_effect=capture_create_template)
        
        await bootstrap_default_template()
        
        # Find Incident Response section with notification tree (contacts)
        ir_section = next(
            (s for s in captured_schema['sections'] if s['section_id'] == 'incident_response'),
            None
        )
        assert ir_section is not None
        
        # Find notification_tree table (contact list)
        notification_tree = next(
            (f for f in ir_section['fields'] if f['field_id'] == 'notification_tree'),
            None
        )
        assert notification_tree is not None
        assert notification_tree['field_type'] == 'table'
        assert 'columns' in notification_tree
        
        # Verify contact-related columns
        column_ids = [c['column_id'] for c in notification_tree['columns']]
        assert 'contact' in column_ids
        assert 'role_title' in column_ids


@pytest.mark.asyncio
async def test_bootstrap_includes_vendor_dependencies():
    """Test that bootstrap includes vendor dependencies in BIA."""
    with patch('app.repositories.bc3') as mock_repo:
        mock_repo.get_default_template = AsyncMock(return_value=None)
        
        captured_schema = None
        
        async def capture_create_template(*args, **kwargs):
            nonlocal captured_schema
            captured_schema = kwargs.get('schema_json')
            return {'id': 1, 'schema_json': captured_schema}
        
        mock_repo.create_template = AsyncMock(side_effect=capture_create_template)
        
        await bootstrap_default_template()
        
        # Find BIA section
        bia_section = next(
            (s for s in captured_schema['sections'] if s['section_id'] == 'business_impact_analysis'),
            None
        )
        assert bia_section is not None
        
        # Find process_dependencies table (includes vendors)
        dependencies_table = next(
            (f for f in bia_section['fields'] if f['field_id'] == 'process_dependencies'),
            None
        )
        assert dependencies_table is not None
        assert dependencies_table['field_type'] == 'table'
        assert 'columns' in dependencies_table
        
        # Verify vendor-related column
        column_ids = [c['column_id'] for c in dependencies_table['columns']]
        assert 'dependent_vendors' in column_ids


@pytest.mark.asyncio
async def test_bootstrap_preserves_section_order():
    """Test that bootstrap preserves the correct section order."""
    with patch('app.repositories.bc3') as mock_repo:
        mock_repo.get_default_template = AsyncMock(return_value=None)
        
        captured_schema = None
        
        async def capture_create_template(*args, **kwargs):
            nonlocal captured_schema
            captured_schema = kwargs.get('schema_json')
            return {'id': 1, 'schema_json': captured_schema}
        
        mock_repo.create_template = AsyncMock(side_effect=capture_create_template)
        
        await bootstrap_default_template()
        
        # Verify sections are ordered
        sections = captured_schema['sections']
        orders = [s['order'] for s in sections]
        assert orders == sorted(orders), "Sections should be in ascending order"
        
        # Verify expected sections in order
        section_ids = [s['section_id'] for s in sections]
        expected_first_sections = [
            'plan_overview',
            'governance_roles',
            'business_impact_analysis',
            'risk_assessment',
        ]
        
        for i, expected_id in enumerate(expected_first_sections):
            assert section_ids[i] == expected_id, f"Section at position {i} should be {expected_id}"


@pytest.mark.asyncio
async def test_bootstrap_includes_default_placeholders():
    """Test that bootstrap includes help_text as default placeholders."""
    with patch('app.repositories.bc3') as mock_repo:
        mock_repo.get_default_template = AsyncMock(return_value=None)
        
        captured_schema = None
        
        async def capture_create_template(*args, **kwargs):
            nonlocal captured_schema
            captured_schema = kwargs.get('schema_json')
            return {'id': 1, 'schema_json': captured_schema}
        
        mock_repo.create_template = AsyncMock(side_effect=capture_create_template)
        
        await bootstrap_default_template()
        
        # Count fields with help_text (default placeholders)
        fields_with_placeholders = 0
        total_fields = 0
        
        for section in captured_schema['sections']:
            for field in section['fields']:
                total_fields += 1
                if field.get('help_text'):
                    fields_with_placeholders += 1
        
        # Most fields should have help_text as placeholders
        assert fields_with_placeholders > 0
        assert fields_with_placeholders / total_fields > 0.5, \
            "Majority of fields should have help_text placeholders"
