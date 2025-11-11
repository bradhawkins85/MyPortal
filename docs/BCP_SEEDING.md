# BCP Seeding System Documentation

## Overview

The BCP (Business Continuity Planning) module automatically seeds new plans with sensible defaults to enable teams to start immediately. This document explains the seeding system, what defaults are included, and how to manage them.

## Automatic Seeding

When a new BCP plan is created for a company, it is automatically populated with the following defaults:

### 1. Plan Objectives (5 items)
- Perform risk assessment
- Identify & prioritize critical activities
- Document immediate incident response
- Document recovery strategies/actions
- Review & update plan regularly

**Edit Location**: BCP Overview page → Plan Details section

### 2. Immediate Response Checklist (18 items)
Essential actions to take during an incident:
- Assess incident severity
- Evacuate if required
- Account for all personnel
- Identify injuries
- Contact emergency services
- Implement incident response plan
- Start event log
- Activate staff/resources
- Appoint spokesperson
- Prioritize information gathering
- Brief team on incident
- Allocate roles/responsibilities
- Identify damage
- Identify disrupted critical activities
- Keep staff informed
- Contact key stakeholders
- Ensure regulatory/compliance requirements are met
- Initiate media/PR response

**Edit Location**: Incident Console page → Checklist tab (during active incident)

### 3. Crisis & Recovery Checklist (23 items)
Post-crisis actions covering:
- Recording injuries and damage
- Documentation and evidence collection
- Staff debriefing and support
- Communication and updates
- Insurance claims
- Government support and financial arrangements
- Tax office contact
- Wellbeing resources
- Lessons learned
- Plan updates

**Edit Location**: Recovery page → Crisis & Recovery Checklist section

### 4. Emergency Kit Items (24 items)
- **Documents (14 items)**: BCP copy, staff contacts, customer/supplier lists, emergency contacts, site plans, evacuation plans, inventory, insurance details, banking info, engineering drawings, product specs, formulas/trade secrets, local authority contacts, letterhead/stamps/seals
- **Equipment (10 items)**: Backup media, spare keys/codes, torch + batteries, hazard tape, message pads + flip chart, markers, stationery, mobile phone + charger, dust/fume masks, disposable camera

**Edit Location**: Incident Console page → Emergency Kit tab

### 5. Example Risks (2 items)
Sample risk scenarios demonstrating risk assessment methodology:
- Production interruption (e.g., equipment breakdown or fire)
- Burglary

**Edit Location**: Risk Assessment page

## Risk Assessment Scales

The seeding system includes comprehensive risk assessment scales:

### Likelihood Scale (1-4)
1. **Unlikely** - Rare occurrence, may happen once in 10+ years
2. **Possible** - Could happen, once in 3-10 years
3. **Moderate** - Might happen, once per year to once in 3 years
4. **Likely** - Expected to happen, multiple times per year

### Impact Scale (1-4)
1. **Minimal** - Minor inconvenience, no significant business impact
2. **Minor** - Some disruption, temporary impact on operations
3. **Moderate** - Significant disruption, notable impact on key activities
4. **Major** - Severe impact, threatens business viability

### Severity Bands (Rating = Likelihood × Impact)
- **Low (1-2)**: Monitor and accept
- **Medium (3-6)**: Reduce likelihood or impact
- **High (8-12)**: Priority risk mitigation required
- **Extreme (16)**: Immediate action required

## Re-seeding Defaults

### When to Re-seed

Re-seed defaults when:
- Items have been accidentally deleted and you want to restore them
- You want to see the default templates again
- A new team member needs reference examples

### How to Re-seed

1. Navigate to `/bcp/admin/seed-info` (BCP Admin → Seed Data)
2. Click "Re-seed Defaults" button
3. Select which categories you want to re-seed:
   - Objectives
   - Immediate Response Checklist
   - Crisis & Recovery Checklist
   - Emergency Kit Items
   - Example Risks
4. Click "Re-seed Selected"

### Idempotency Guarantee

Re-seeding is **idempotent** - it will only add items that are missing. If a category already has items, re-seeding will **not** create duplicates. This makes it safe to re-run seeding operations without worrying about data duplication.

## Modifying Defaults

All seeded defaults can be:
- **Edited** - Customize the text, values, or details
- **Deleted** - Remove items you don't need
- **Added to** - Create your own custom items alongside defaults

Consider keeping example items for reference while building your own customized plan.

## Integration with Plan Creation

The seeding system is automatically triggered when:
1. A new BCP plan is created for a company
2. A company first accesses the BCP module

No manual action is required - the system seeds defaults automatically to provide a working starting point.

## API Endpoints

### View Seeding Information
```
GET /bcp/admin/seed-info
```
Displays complete seeding documentation and risk scales.

### Re-seed Defaults
```
POST /bcp/admin/reseed
Form Data:
- categories: List of category names to re-seed (optional, defaults to all)
```

## Permission Requirements

- **Viewing seed info**: Requires `bcp:view` permission
- **Re-seeding defaults**: Requires `bcp:edit` permission
- **Super admins**: Have full access to all seeding features

## Development Notes

### Code Organization
- **Service**: `app/services/bcp_seeding.py` - Core seeding logic
- **Routes**: `app/api/routes/bcp.py` - Admin endpoints
- **Templates**: `app/templates/bcp/seed_info.html` - Admin UI
- **Tests**: `tests/bcp/test_seeding.py` - Seeding test suite

### Adding New Default Content

To add new default content to the seeding system:

1. Create a seed function in the appropriate repository (e.g., `app/repositories/bcp.py`)
2. Add the function call to `seed_new_plan_defaults()` in `app/services/bcp_seeding.py`
3. Update the `reseed_plan_defaults()` function to support the new category
4. Add category documentation to `get_seeding_documentation()`
5. Update the UI template with the new category
6. Add tests for the new seeding function

## Support

For issues or questions about the seeding system:
1. Check the seed info page at `/bcp/admin/seed-info`
2. Review audit logs for seeding actions
3. Contact system administrators
