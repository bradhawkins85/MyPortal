"""
BCP Seeding Service - Centralized seeding logic for Business Continuity Plans.

This service provides functions to seed new BCP plans with sensible defaults
so teams can start immediately. All seeding operations are idempotent to allow
re-seeding without duplication.
"""
from __future__ import annotations

from app.repositories import bcp as bcp_repo


async def seed_new_plan_defaults(plan_id: int) -> dict[str, int]:
    """
    Seed a new BCP plan with all default content.
    
    This function is called when a new plan is created to populate it with:
    - 5 default objectives (Issue 01)
    - 18 immediate response checklist items (Issue 06)
    - 23 crisis & recovery checklist items (Issue 09)
    - Document and equipment emergency kit items (Issue 08)
    - 2 example risks (Issue 03)
    
    Args:
        plan_id: The ID of the BCP plan to seed
        
    Returns:
        Dictionary with counts of seeded items by category
    """
    stats = {
        "objectives": 0,
        "immediate_checklist": 0,
        "crisis_recovery_checklist": 0,
        "emergency_kit_documents": 0,
        "emergency_kit_equipment": 0,
        "example_risks": 0,
    }
    
    # Seed objectives (5 defaults)
    existing_objectives = await bcp_repo.list_objectives(plan_id)
    if not existing_objectives:
        await bcp_repo.seed_default_objectives(plan_id)
        stats["objectives"] = 5
    
    # Seed immediate response checklist (18 items)
    existing_immediate = await bcp_repo.list_checklist_items(plan_id, phase="Immediate")
    if not existing_immediate:
        await bcp_repo.seed_default_checklist_items(plan_id)
        stats["immediate_checklist"] = 18
    
    # Seed crisis & recovery checklist (23 items)
    existing_crisis = await bcp_repo.list_checklist_items(plan_id, phase="CrisisRecovery")
    if not existing_crisis:
        await bcp_repo.seed_default_crisis_recovery_checklist_items(plan_id)
        stats["crisis_recovery_checklist"] = 23
    
    # Seed emergency kit items (documents and equipment)
    existing_kit = await bcp_repo.list_emergency_kit_items(plan_id)
    if not existing_kit:
        await bcp_repo.seed_default_emergency_kit_items(plan_id)
        # Count by category
        kit_items = await bcp_repo.list_emergency_kit_items(plan_id)
        stats["emergency_kit_documents"] = len([i for i in kit_items if i["category"] == "Document"])
        stats["emergency_kit_equipment"] = len([i for i in kit_items if i["category"] == "Equipment"])
    
    # Seed example risks (2 risks)
    existing_risks = await bcp_repo.list_risks(plan_id)
    if not existing_risks:
        await bcp_repo.seed_example_risks(plan_id)
        stats["example_risks"] = 2
    
    return stats


async def reseed_plan_defaults(plan_id: int, categories: list[str] | None = None) -> dict[str, int]:
    """
    Re-seed specific categories of defaults for an existing plan.
    
    This function allows selective re-seeding of defaults. It's idempotent -
    it will only add items that don't already exist, preventing duplication.
    
    Args:
        plan_id: The ID of the BCP plan to re-seed
        categories: List of categories to re-seed. If None, re-seeds all.
                   Valid categories: "objectives", "immediate_checklist",
                   "crisis_recovery_checklist", "emergency_kit", "example_risks"
                   
    Returns:
        Dictionary with counts of newly seeded items by category
    """
    if categories is None:
        categories = [
            "objectives",
            "immediate_checklist",
            "crisis_recovery_checklist",
            "emergency_kit",
            "example_risks",
        ]
    
    stats = {
        "objectives": 0,
        "immediate_checklist": 0,
        "crisis_recovery_checklist": 0,
        "emergency_kit_documents": 0,
        "emergency_kit_equipment": 0,
        "example_risks": 0,
    }
    
    # Re-seed objectives if requested and missing
    if "objectives" in categories:
        existing_objectives = await bcp_repo.list_objectives(plan_id)
        if not existing_objectives:
            await bcp_repo.seed_default_objectives(plan_id)
            stats["objectives"] = 5
    
    # Re-seed immediate response checklist if requested and missing
    if "immediate_checklist" in categories:
        existing_immediate = await bcp_repo.list_checklist_items(plan_id, phase="Immediate")
        if not existing_immediate:
            await bcp_repo.seed_default_checklist_items(plan_id)
            stats["immediate_checklist"] = 18
    
    # Re-seed crisis & recovery checklist if requested and missing
    if "crisis_recovery_checklist" in categories:
        existing_crisis = await bcp_repo.list_checklist_items(plan_id, phase="CrisisRecovery")
        if not existing_crisis:
            await bcp_repo.seed_default_crisis_recovery_checklist_items(plan_id)
            stats["crisis_recovery_checklist"] = 23
    
    # Re-seed emergency kit items if requested and missing
    if "emergency_kit" in categories:
        existing_kit = await bcp_repo.list_emergency_kit_items(plan_id)
        if not existing_kit:
            await bcp_repo.seed_default_emergency_kit_items(plan_id)
            # Count by category
            kit_items = await bcp_repo.list_emergency_kit_items(plan_id)
            stats["emergency_kit_documents"] = len([i for i in kit_items if i["category"] == "Document"])
            stats["emergency_kit_equipment"] = len([i for i in kit_items if i["category"] == "Equipment"])
    
    # Re-seed example risks if requested and missing
    if "example_risks" in categories:
        existing_risks = await bcp_repo.list_risks(plan_id)
        if not existing_risks:
            await bcp_repo.seed_example_risks(plan_id)
            stats["example_risks"] = 2
    
    return stats


def get_seeding_documentation() -> dict[str, any]:
    """
    Get documentation about seeding defaults and how to manage them.
    
    Returns:
        Dictionary with documentation about seeding behavior
    """
    return {
        "description": "BCP plans are automatically seeded with sensible defaults when first created.",
        "categories": [
            {
                "name": "objectives",
                "title": "Plan Objectives",
                "count": 5,
                "description": "Default objectives covering risk assessment, critical activities, incident response, recovery strategies, and regular review.",
                "can_reseed": True,
                "edit_location": "BCP Overview page - Plan Details section",
            },
            {
                "name": "immediate_checklist",
                "title": "Immediate Response Checklist",
                "count": 18,
                "description": "Initial response actions to take during an incident, covering evacuation, personnel accounting, emergency services, and stakeholder communication.",
                "can_reseed": True,
                "edit_location": "Incident Console page - Checklist tab (during active incident)",
            },
            {
                "name": "crisis_recovery_checklist",
                "title": "Crisis & Recovery Checklist",
                "count": 23,
                "description": "Post-crisis actions covering documentation, staff support, insurance claims, financial arrangements, and lessons learned.",
                "can_reseed": True,
                "edit_location": "Recovery page - Crisis & Recovery Checklist section",
            },
            {
                "name": "emergency_kit",
                "title": "Emergency Kit Items",
                "count": 24,  # 14 documents + 10 equipment
                "description": "Essential documents and equipment for emergency preparedness, including BCP copies, contact lists, backup media, and emergency supplies.",
                "can_reseed": True,
                "edit_location": "Incident Console page - Emergency Kit tab",
            },
            {
                "name": "example_risks",
                "title": "Example Risks",
                "count": 2,
                "description": "Sample risk scenarios demonstrating risk assessment methodology, including production interruption and burglary scenarios.",
                "can_reseed": True,
                "edit_location": "Risk Assessment page",
            },
        ],
        "risk_scales": {
            "likelihood": [
                {"value": 1, "label": "Unlikely", "description": "Rare occurrence, may happen once in 10+ years"},
                {"value": 2, "label": "Possible", "description": "Could happen, once in 3-10 years"},
                {"value": 3, "label": "Moderate", "description": "Might happen, once per year to once in 3 years"},
                {"value": 4, "label": "Likely", "description": "Expected to happen, multiple times per year"},
            ],
            "impact": [
                {"value": 1, "label": "Minimal", "description": "Minor inconvenience, no significant business impact"},
                {"value": 2, "label": "Minor", "description": "Some disruption, temporary impact on operations"},
                {"value": 3, "label": "Moderate", "description": "Significant disruption, notable impact on key activities"},
                {"value": 4, "label": "Major", "description": "Severe impact, threatens business viability"},
            ],
            "severity_bands": [
                {"range": "1-2", "label": "Low", "color": "green", "action": "Monitor and accept"},
                {"range": "3-6", "label": "Medium", "color": "yellow", "action": "Reduce likelihood or impact"},
                {"range": "8-12", "label": "High", "color": "orange", "action": "Priority risk mitigation required"},
                {"range": "16", "label": "Extreme", "color": "red", "action": "Immediate action required"},
            ],
        },
        "idempotency": "Re-seeding is idempotent. It will only add defaults that are missing, never duplicating existing items.",
        "modification_guidance": "All seeded defaults can be edited, deleted, or customized through the respective admin UI pages. Consider keeping examples for reference while building your own plan.",
    }
