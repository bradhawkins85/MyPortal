"""Service for generating BCP template schemas."""

from typing import Any

from app.schemas.bcp_template import (
    BCPTemplateSchema,
    FieldChoice,
    FieldType,
    TableColumn,
    TemplateField,
    TemplateMetadata,
    TemplateSection,
)


def get_default_government_bcp_template() -> BCPTemplateSchema:
    """
    Generate the default government BCP template schema.
    
    This template follows best practices for government business continuity planning
    and includes all standard sections required for comprehensive DR/IR/BC planning.
    
    Returns:
        BCPTemplateSchema: Complete template schema with sections, fields, and metadata
    """
    metadata = TemplateMetadata(
        template_name="Government Business Continuity Plan",
        template_version="1.0",
        description="Comprehensive business continuity plan template following government standards",
        requires_approval=True,
        approval_workflow="Plans require approval from business unit head, risk management, and executive sponsor",
        revision_tracking=True,
        attachments_required=[
            "Emergency Contact List",
            "Vendor SLA Documentation",
            "Site Information Sheets",
            "System Inventory",
            "Floor Plans and Facility Diagrams",
        ],
    )
    
    sections = [
        # 1. Plan Overview
        TemplateSection(
            section_id="plan_overview",
            title="Plan Overview",
            description="High-level overview of the business continuity plan",
            order=1,
            parent_section_id=None,
            fields=[
                TemplateField(
                    field_id="purpose",
                    label="Purpose",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="Describe the purpose and objectives of this business continuity plan",
                ),
                TemplateField(
                    field_id="scope",
                    label="Scope",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="Define what business units, processes, and systems are covered by this plan",
                ),
                TemplateField(
                    field_id="objectives",
                    label="Objectives",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="List the key objectives this plan aims to achieve",
                ),
                TemplateField(
                    field_id="assumptions",
                    label="Assumptions",
                    field_type=FieldType.RICH_TEXT,
                    required=False,
                    help_text="Document key assumptions made in developing this plan",
                ),
            ],
        ),
        
        # 2. Governance & Roles
        TemplateSection(
            section_id="governance_roles",
            title="Governance & Roles",
            description="Roles, responsibilities, and governance structure",
            order=2,
            parent_section_id=None,
            fields=[
                TemplateField(
                    field_id="plan_owner",
                    label="Plan Owner",
                    field_type=FieldType.USER_REF,
                    required=True,
                    help_text="Primary owner responsible for maintaining this plan",
                ),
                TemplateField(
                    field_id="roles_responsibilities",
                    label="Roles and Responsibilities",
                    field_type=FieldType.TABLE,
                    required=True,
                    help_text="Define key roles and their responsibilities during a business continuity event",
                    columns=[
                        TableColumn(
                            column_id="role_name",
                            label="Role Name",
                            field_type=FieldType.TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="responsibilities",
                            label="Responsibilities",
                            field_type=FieldType.RICH_TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="primary_contact",
                            label="Primary Contact",
                            field_type=FieldType.CONTACT_REF,
                            required=True,
                        ),
                        TableColumn(
                            column_id="backup_contact",
                            label="Backup Contact",
                            field_type=FieldType.CONTACT_REF,
                            required=False,
                        ),
                    ],
                ),
                TemplateField(
                    field_id="escalation_matrix",
                    label="Escalation Matrix",
                    field_type=FieldType.TABLE,
                    required=True,
                    help_text="Define the escalation path for different severity levels",
                    columns=[
                        TableColumn(
                            column_id="severity_level",
                            label="Severity Level",
                            field_type=FieldType.SELECT,
                            required=True,
                            choices=[
                                FieldChoice(value="critical", label="Critical"),
                                FieldChoice(value="high", label="High"),
                                FieldChoice(value="medium", label="Medium"),
                                FieldChoice(value="low", label="Low"),
                            ],
                        ),
                        TableColumn(
                            column_id="escalation_contact",
                            label="Escalation Contact",
                            field_type=FieldType.CONTACT_REF,
                            required=True,
                        ),
                        TableColumn(
                            column_id="escalation_timeframe",
                            label="Escalation Timeframe",
                            field_type=FieldType.TEXT,
                            required=True,
                            help_text="e.g., 'Immediate', '30 minutes', '2 hours'",
                        ),
                    ],
                ),
                TemplateField(
                    field_id="approval_authority",
                    label="Approval Authority",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="Describe who has authority to approve plan activation and key decisions",
                ),
            ],
        ),
        
        # 3. Business Impact Analysis (BIA)
        TemplateSection(
            section_id="business_impact_analysis",
            title="Business Impact Analysis (BIA)",
            description="Analysis of critical business processes and their impact if disrupted",
            order=3,
            parent_section_id=None,
            fields=[
                TemplateField(
                    field_id="critical_processes",
                    label="Critical Business Processes",
                    field_type=FieldType.TABLE,
                    required=True,
                    help_text="Identify and analyze critical business processes",
                    columns=[
                        TableColumn(
                            column_id="process_name",
                            label="Process Name",
                            field_type=FieldType.TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="description",
                            label="Description",
                            field_type=FieldType.RICH_TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="financial_impact",
                            label="Financial Impact",
                            field_type=FieldType.SELECT,
                            required=True,
                            choices=[
                                FieldChoice(value="critical", label="Critical (>$1M/day)"),
                                FieldChoice(value="high", label="High ($100K-$1M/day)"),
                                FieldChoice(value="medium", label="Medium ($10K-$100K/day)"),
                                FieldChoice(value="low", label="Low (<$10K/day)"),
                            ],
                        ),
                        TableColumn(
                            column_id="operational_impact",
                            label="Operational Impact",
                            field_type=FieldType.SELECT,
                            required=True,
                            choices=[
                                FieldChoice(value="critical", label="Critical"),
                                FieldChoice(value="high", label="High"),
                                FieldChoice(value="medium", label="Medium"),
                                FieldChoice(value="low", label="Low"),
                            ],
                        ),
                        TableColumn(
                            column_id="legal_impact",
                            label="Legal/Regulatory Impact",
                            field_type=FieldType.SELECT,
                            required=True,
                            choices=[
                                FieldChoice(value="critical", label="Critical"),
                                FieldChoice(value="high", label="High"),
                                FieldChoice(value="medium", label="Medium"),
                                FieldChoice(value="low", label="Low"),
                            ],
                        ),
                        TableColumn(
                            column_id="reputation_impact",
                            label="Reputation Impact",
                            field_type=FieldType.SELECT,
                            required=True,
                            choices=[
                                FieldChoice(value="critical", label="Critical"),
                                FieldChoice(value="high", label="High"),
                                FieldChoice(value="medium", label="Medium"),
                                FieldChoice(value="low", label="Low"),
                            ],
                        ),
                        TableColumn(
                            column_id="rto",
                            label="RTO (Recovery Time Objective)",
                            field_type=FieldType.TEXT,
                            required=True,
                            help_text="Maximum acceptable downtime, e.g., '4 hours', '24 hours'",
                        ),
                        TableColumn(
                            column_id="rpo",
                            label="RPO (Recovery Point Objective)",
                            field_type=FieldType.TEXT,
                            required=True,
                            help_text="Maximum acceptable data loss, e.g., '1 hour', '24 hours'",
                        ),
                        TableColumn(
                            column_id="mtpd",
                            label="MTPD (Maximum Tolerable Period of Disruption)",
                            field_type=FieldType.TEXT,
                            required=True,
                            help_text="Absolute maximum downtime before severe consequences, e.g., '7 days'",
                        ),
                    ],
                ),
                TemplateField(
                    field_id="process_dependencies",
                    label="Process Dependencies",
                    field_type=FieldType.TABLE,
                    required=True,
                    help_text="Document dependencies between processes, systems, vendors, and sites",
                    is_computed=True,
                    computation_note="Cross-references critical processes with their dependencies",
                    columns=[
                        TableColumn(
                            column_id="process_name",
                            label="Process Name",
                            field_type=FieldType.TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="dependent_systems",
                            label="Dependent Systems",
                            field_type=FieldType.TEXT,
                            required=False,
                            help_text="IT systems this process depends on",
                        ),
                        TableColumn(
                            column_id="dependent_vendors",
                            label="Dependent Vendors",
                            field_type=FieldType.TEXT,
                            required=False,
                            help_text="Third-party vendors this process depends on",
                        ),
                        TableColumn(
                            column_id="dependent_sites",
                            label="Dependent Sites",
                            field_type=FieldType.TEXT,
                            required=False,
                            help_text="Physical locations this process depends on",
                        ),
                        TableColumn(
                            column_id="key_personnel",
                            label="Key Personnel",
                            field_type=FieldType.TEXT,
                            required=False,
                            help_text="Critical staff required for this process",
                        ),
                    ],
                ),
            ],
        ),
        
        # 4. Risk Assessment
        TemplateSection(
            section_id="risk_assessment",
            title="Risk Assessment",
            description="Identification and assessment of threats and risks",
            order=4,
            parent_section_id=None,
            fields=[
                TemplateField(
                    field_id="identified_risks",
                    label="Identified Risks and Threats",
                    field_type=FieldType.TABLE,
                    required=True,
                    help_text="Document identified threats and their risk ratings",
                    columns=[
                        TableColumn(
                            column_id="threat_name",
                            label="Threat/Risk Name",
                            field_type=FieldType.TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="description",
                            label="Description",
                            field_type=FieldType.RICH_TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="likelihood",
                            label="Likelihood",
                            field_type=FieldType.SELECT,
                            required=True,
                            choices=[
                                FieldChoice(value="very_high", label="Very High (>80%)"),
                                FieldChoice(value="high", label="High (60-80%)"),
                                FieldChoice(value="medium", label="Medium (40-60%)"),
                                FieldChoice(value="low", label="Low (20-40%)"),
                                FieldChoice(value="very_low", label="Very Low (<20%)"),
                            ],
                        ),
                        TableColumn(
                            column_id="impact",
                            label="Impact",
                            field_type=FieldType.SELECT,
                            required=True,
                            choices=[
                                FieldChoice(value="critical", label="Critical"),
                                FieldChoice(value="high", label="High"),
                                FieldChoice(value="medium", label="Medium"),
                                FieldChoice(value="low", label="Low"),
                            ],
                        ),
                        TableColumn(
                            column_id="risk_rating",
                            label="Risk Rating",
                            field_type=FieldType.SELECT,
                            required=True,
                            is_computed=True,
                            help_text="Computed from likelihood and impact",
                            choices=[
                                FieldChoice(value="critical", label="Critical"),
                                FieldChoice(value="high", label="High"),
                                FieldChoice(value="medium", label="Medium"),
                                FieldChoice(value="low", label="Low"),
                            ],
                        ),
                        TableColumn(
                            column_id="mitigation_strategies",
                            label="Mitigation Strategies",
                            field_type=FieldType.RICH_TEXT,
                            required=True,
                            help_text="Strategies to reduce or mitigate this risk",
                        ),
                    ],
                ),
            ],
        ),
        
        # 5. Recovery Strategies
        TemplateSection(
            section_id="recovery_strategies",
            title="Recovery Strategies",
            description="Strategies for recovering critical business processes",
            order=5,
            parent_section_id=None,
            fields=[
                TemplateField(
                    field_id="process_recovery_strategies",
                    label="Process-Level Recovery Strategies",
                    field_type=FieldType.TABLE,
                    required=True,
                    help_text="Define recovery strategies for each critical process",
                    columns=[
                        TableColumn(
                            column_id="process_name",
                            label="Process Name",
                            field_type=FieldType.TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="recovery_strategy",
                            label="Recovery Strategy",
                            field_type=FieldType.RICH_TEXT,
                            required=True,
                            help_text="Detailed recovery approach for this process",
                        ),
                        TableColumn(
                            column_id="workarounds",
                            label="Workarounds",
                            field_type=FieldType.RICH_TEXT,
                            required=False,
                            help_text="Alternative methods if primary recovery is unavailable",
                        ),
                        TableColumn(
                            column_id="resource_requirements",
                            label="Resource Requirements",
                            field_type=FieldType.RICH_TEXT,
                            required=True,
                            help_text="Personnel, equipment, facilities, etc. required for recovery",
                        ),
                        TableColumn(
                            column_id="estimated_recovery_time",
                            label="Estimated Recovery Time",
                            field_type=FieldType.TEXT,
                            required=True,
                        ),
                    ],
                ),
            ],
        ),
        
        # 6. Incident Response
        TemplateSection(
            section_id="incident_response",
            title="Incident Response",
            description="Procedures for responding to and managing incidents",
            order=6,
            parent_section_id=None,
            fields=[
                TemplateField(
                    field_id="activation_criteria",
                    label="Plan Activation Criteria",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="Define when and how this plan should be activated",
                ),
                TemplateField(
                    field_id="notification_tree",
                    label="Notification Tree",
                    field_type=FieldType.TABLE,
                    required=True,
                    help_text="Define the notification sequence and contact information",
                    columns=[
                        TableColumn(
                            column_id="notification_order",
                            label="Order",
                            field_type=FieldType.INTEGER,
                            required=True,
                            min_value=1,
                        ),
                        TableColumn(
                            column_id="role_title",
                            label="Role/Title",
                            field_type=FieldType.TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="contact",
                            label="Contact",
                            field_type=FieldType.CONTACT_REF,
                            required=True,
                        ),
                        TableColumn(
                            column_id="notification_method",
                            label="Notification Method",
                            field_type=FieldType.MULTISELECT,
                            required=True,
                            choices=[
                                FieldChoice(value="phone", label="Phone"),
                                FieldChoice(value="email", label="Email"),
                                FieldChoice(value="sms", label="SMS"),
                                FieldChoice(value="pager", label="Pager"),
                            ],
                        ),
                    ],
                ),
                TemplateField(
                    field_id="response_procedures",
                    label="Step-by-Step Response Procedures",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="Detailed procedures for responding to an incident",
                ),
                TemplateField(
                    field_id="initial_assessment_checklist",
                    label="Initial Assessment Checklist",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="Checklist for initial incident assessment",
                ),
            ],
        ),
        
        # 7. Communications Plan
        TemplateSection(
            section_id="communications_plan",
            title="Communications Plan",
            description="Internal and external communication strategies",
            order=7,
            parent_section_id=None,
            fields=[
                TemplateField(
                    field_id="internal_communications",
                    label="Internal Communications Strategy",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="How to communicate with employees during an incident",
                ),
                TemplateField(
                    field_id="external_communications",
                    label="External Communications Strategy",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="How to communicate with customers, partners, and the public",
                ),
                TemplateField(
                    field_id="regulatory_communications",
                    label="Regulatory Communications",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="Requirements for notifying regulators and government agencies",
                ),
                TemplateField(
                    field_id="media_relations",
                    label="Media Relations",
                    field_type=FieldType.RICH_TEXT,
                    required=False,
                    help_text="Strategy for handling media inquiries",
                ),
                TemplateField(
                    field_id="communication_templates",
                    label="Communication Templates",
                    field_type=FieldType.TABLE,
                    required=False,
                    help_text="Pre-approved communication templates",
                    columns=[
                        TableColumn(
                            column_id="template_name",
                            label="Template Name",
                            field_type=FieldType.TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="audience",
                            label="Audience",
                            field_type=FieldType.SELECT,
                            required=True,
                            choices=[
                                FieldChoice(value="internal", label="Internal Staff"),
                                FieldChoice(value="customers", label="Customers"),
                                FieldChoice(value="partners", label="Partners"),
                                FieldChoice(value="regulators", label="Regulators"),
                                FieldChoice(value="media", label="Media"),
                            ],
                        ),
                        TableColumn(
                            column_id="template_content",
                            label="Template Content",
                            field_type=FieldType.RICH_TEXT,
                            required=True,
                        ),
                    ],
                ),
            ],
        ),
        
        # 8. IT/Systems Recovery
        TemplateSection(
            section_id="it_systems_recovery",
            title="IT/Systems Recovery",
            description="IT infrastructure and application recovery procedures",
            order=8,
            parent_section_id=None,
            fields=[
                TemplateField(
                    field_id="critical_applications",
                    label="Critical Applications",
                    field_type=FieldType.TABLE,
                    required=True,
                    help_text="List of critical IT applications and their recovery procedures",
                    columns=[
                        TableColumn(
                            column_id="application_name",
                            label="Application Name",
                            field_type=FieldType.TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="description",
                            label="Description",
                            field_type=FieldType.RICH_TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="recovery_priority",
                            label="Recovery Priority",
                            field_type=FieldType.SELECT,
                            required=True,
                            choices=[
                                FieldChoice(value="critical", label="Critical (0-4 hours)"),
                                FieldChoice(value="high", label="High (4-24 hours)"),
                                FieldChoice(value="medium", label="Medium (1-3 days)"),
                                FieldChoice(value="low", label="Low (>3 days)"),
                            ],
                        ),
                        TableColumn(
                            column_id="recovery_runbook",
                            label="Recovery Runbook",
                            field_type=FieldType.URL,
                            required=False,
                            help_text="Link to detailed recovery runbook",
                        ),
                    ],
                ),
                TemplateField(
                    field_id="infrastructure_recovery",
                    label="Infrastructure Recovery",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="Procedures for recovering critical IT infrastructure",
                ),
                TemplateField(
                    field_id="backup_restore_procedures",
                    label="Backup and Restore Procedures",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="Detailed backup and restore procedures",
                ),
                TemplateField(
                    field_id="dr_site_information",
                    label="Disaster Recovery Site Information",
                    field_type=FieldType.RICH_TEXT,
                    required=False,
                    help_text="Information about DR sites and failover procedures",
                ),
                TemplateField(
                    field_id="test_cadence",
                    label="Testing Cadence",
                    field_type=FieldType.SELECT,
                    required=True,
                    help_text="How frequently IT recovery procedures should be tested",
                    choices=[
                        FieldChoice(value="monthly", label="Monthly"),
                        FieldChoice(value="quarterly", label="Quarterly"),
                        FieldChoice(value="semi_annually", label="Semi-Annually"),
                        FieldChoice(value="annually", label="Annually"),
                    ],
                ),
            ],
        ),
        
        # 9. Testing & Exercises
        TemplateSection(
            section_id="testing_exercises",
            title="Testing & Exercises",
            description="Plan testing and exercise schedule",
            order=9,
            parent_section_id=None,
            fields=[
                TemplateField(
                    field_id="test_schedule",
                    label="Testing Schedule",
                    field_type=FieldType.TABLE,
                    required=True,
                    help_text="Schedule for testing various aspects of the plan",
                    columns=[
                        TableColumn(
                            column_id="test_type",
                            label="Test Type",
                            field_type=FieldType.SELECT,
                            required=True,
                            choices=[
                                FieldChoice(value="tabletop", label="Tabletop Exercise"),
                                FieldChoice(value="walkthrough", label="Walkthrough"),
                                FieldChoice(value="simulation", label="Simulation"),
                                FieldChoice(value="full_test", label="Full Test"),
                            ],
                        ),
                        TableColumn(
                            column_id="frequency",
                            label="Frequency",
                            field_type=FieldType.SELECT,
                            required=True,
                            choices=[
                                FieldChoice(value="monthly", label="Monthly"),
                                FieldChoice(value="quarterly", label="Quarterly"),
                                FieldChoice(value="semi_annually", label="Semi-Annually"),
                                FieldChoice(value="annually", label="Annually"),
                            ],
                        ),
                        TableColumn(
                            column_id="responsible_party",
                            label="Responsible Party",
                            field_type=FieldType.USER_REF,
                            required=True,
                        ),
                        TableColumn(
                            column_id="next_scheduled_date",
                            label="Next Scheduled Date",
                            field_type=FieldType.DATE,
                            required=False,
                        ),
                    ],
                ),
                TemplateField(
                    field_id="test_scenarios",
                    label="Test Scenarios",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="Describe scenarios to be tested",
                ),
                TemplateField(
                    field_id="evidence_requirements",
                    label="Evidence Requirements",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="What evidence must be collected during tests",
                ),
                TemplateField(
                    field_id="success_criteria",
                    label="Success Criteria",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="Criteria for determining if tests were successful",
                ),
            ],
        ),
        
        # 10. Maintenance & Review
        TemplateSection(
            section_id="maintenance_review",
            title="Maintenance & Review",
            description="Plan maintenance and review procedures",
            order=10,
            parent_section_id=None,
            fields=[
                TemplateField(
                    field_id="review_cadence",
                    label="Review Cadence",
                    field_type=FieldType.SELECT,
                    required=True,
                    help_text="How frequently this plan should be reviewed",
                    choices=[
                        FieldChoice(value="monthly", label="Monthly"),
                        FieldChoice(value="quarterly", label="Quarterly"),
                        FieldChoice(value="semi_annually", label="Semi-Annually"),
                        FieldChoice(value="annually", label="Annually"),
                    ],
                ),
                TemplateField(
                    field_id="review_owners",
                    label="Review Owners",
                    field_type=FieldType.TABLE,
                    required=True,
                    help_text="Individuals responsible for reviewing different sections",
                    columns=[
                        TableColumn(
                            column_id="section_name",
                            label="Section Name",
                            field_type=FieldType.TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="owner",
                            label="Owner",
                            field_type=FieldType.USER_REF,
                            required=True,
                        ),
                        TableColumn(
                            column_id="last_review_date",
                            label="Last Review Date",
                            field_type=FieldType.DATE,
                            required=False,
                        ),
                        TableColumn(
                            column_id="next_review_date",
                            label="Next Review Date",
                            field_type=FieldType.DATE,
                            required=False,
                        ),
                    ],
                ),
                TemplateField(
                    field_id="change_process",
                    label="Change Management Process",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="Process for proposing and approving changes to this plan",
                ),
                TemplateField(
                    field_id="distribution_list",
                    label="Distribution List",
                    field_type=FieldType.RICH_TEXT,
                    required=True,
                    help_text="Who should receive copies of this plan and updates",
                ),
            ],
        ),
        
        # 11. Appendices
        TemplateSection(
            section_id="appendices",
            title="Appendices",
            description="Supporting documentation and references",
            order=11,
            parent_section_id=None,
            fields=[
                TemplateField(
                    field_id="contact_list",
                    label="Emergency Contact List",
                    field_type=FieldType.FILE,
                    required=True,
                    help_text="Comprehensive list of emergency contacts",
                ),
                TemplateField(
                    field_id="vendor_slas",
                    label="Vendor SLA Documentation",
                    field_type=FieldType.FILE,
                    required=True,
                    help_text="Service level agreements with critical vendors",
                ),
                TemplateField(
                    field_id="site_information",
                    label="Site Information Sheets",
                    field_type=FieldType.FILE,
                    required=True,
                    help_text="Detailed information about facilities and sites",
                ),
                TemplateField(
                    field_id="system_inventory",
                    label="System Inventory",
                    field_type=FieldType.FILE,
                    required=False,
                    help_text="Complete inventory of IT systems and applications",
                ),
                TemplateField(
                    field_id="floor_plans",
                    label="Floor Plans and Facility Diagrams",
                    field_type=FieldType.FILE,
                    required=False,
                    help_text="Physical layout diagrams for facilities",
                ),
                TemplateField(
                    field_id="additional_documentation",
                    label="Additional Documentation",
                    field_type=FieldType.RICH_TEXT,
                    required=False,
                    help_text="References to other relevant documentation",
                ),
            ],
        ),
        
        # 12. Revision History
        TemplateSection(
            section_id="revision_history",
            title="Revision History",
            description="History of plan revisions and updates",
            order=12,
            parent_section_id=None,
            fields=[
                TemplateField(
                    field_id="revision_history_table",
                    label="Revision History",
                    field_type=FieldType.TABLE,
                    required=True,
                    help_text="Track all revisions to this plan",
                    is_computed=True,
                    computation_note="Automatically populated from plan update history",
                    columns=[
                        TableColumn(
                            column_id="version",
                            label="Version",
                            field_type=FieldType.TEXT,
                            required=True,
                        ),
                        TableColumn(
                            column_id="author",
                            label="Author",
                            field_type=FieldType.USER_REF,
                            required=True,
                        ),
                        TableColumn(
                            column_id="date",
                            label="Date",
                            field_type=FieldType.DATE,
                            required=True,
                        ),
                        TableColumn(
                            column_id="summary",
                            label="Summary of Changes",
                            field_type=FieldType.RICH_TEXT,
                            required=True,
                        ),
                    ],
                ),
            ],
        ),
    ]
    
    return BCPTemplateSchema(metadata=metadata, sections=sections)


async def bootstrap_default_template() -> dict[str, Any]:
    """
    Bootstrap the default government BCP template into the database.
    
    This function loads the default template schema and stores it in the database
    if it doesn't already exist. It ensures that the template instance matches
    the discovered/mapped schema with proper section order, field labels,
    default placeholders, and table schemas.
    
    Returns:
        dict: The created or existing template record from the database
    """
    from app.repositories import bc3 as bc_repo
    
    # Check if default template already exists
    existing_template = await bc_repo.get_default_template()
    if existing_template:
        return existing_template
    
    # Get the default template schema
    template_schema = get_default_government_bcp_template()
    
    # Convert the Pydantic model to JSON for storage
    schema_json = template_schema.model_dump(mode='json')
    
    # Create the template in the database
    template = await bc_repo.create_template(
        name=template_schema.metadata.template_name,
        version=template_schema.metadata.template_version,
        is_default=True,
        schema_json=schema_json,
    )
    
    return template
