# BC06 - Incident Console Feature

## Overview
The Incident Console provides a centralized interface for managing business continuity incidents with three main components:

1. **Immediate Response Checklist**: 18 default checklist items for immediate incident response
2. **Key Contacts**: Internal and external emergency contacts with quick call/email actions
3. **Event Log**: Chronological log of incident events with CSV export capability

## Features

### Start Incident
- Creates a new active incident
- Initializes checklist with completion tracking
- Seeds the first event log entry: "Activate business continuity plan"
- Sends portal alert to distribution list (when configured)

### Checklist Tab
- Displays 18 immediate response checklist items
- Toggle completion status with timestamp and user tracking
- Items include:
  - Assess incident severity
  - Evacuate if required
  - Account for all personnel
  - Identify injuries
  - Contact emergency services
  - And 13 more critical response actions

### Contacts Tab
- Manage internal and external contacts
- Quick access to phone and email
- Fields: Name/Organization, Phone, Email, Role/Agency
- Add, edit, and delete contacts

### Event Log Tab
- Chronological timeline of incident events
- Each entry includes: Timestamp, Initials, Details
- Add new events with custom or current timestamp
- Export to CSV for reporting and audit

### Webhook Integration
Endpoint: `POST /bcp/api/webhook/incident/start`

Payload:
```json
{
  "company_id": 1,
  "source": "UptimeKuma",
  "message": "Service down alert",
  "api_key": "your-api-key"
}
```

Response (success):
```json
{
  "status": "started",
  "incident_id": 1,
  "plan_id": 1,
  "started_at": "2025-11-10T21:00:00Z",
  "message": "Incident started successfully"
}
```

Response (already active):
```json
{
  "status": "already_active",
  "incident_id": 1,
  "message": "An incident is already active for this plan"
}
```

## Database Schema

### bcp_incident
- Stores active and historical incidents
- Status: Active or Closed
- Source: Manual, UptimeKuma, or Other

### bcp_checklist_item
- Default checklist items for each plan
- Phase: Immediate or CrisisRecovery
- Display order for proper sequencing

### bcp_checklist_tick
- Tracks completion of checklist items per incident
- Records who completed the item and when
- Audit trail for compliance

### bcp_contact
- Internal and external emergency contacts
- Kind: Internal or External
- Phone numbers (comma-separated for multiple)
- Email and role/responsibility

### bcp_event_log_entry
- Chronological event log for incidents
- Timestamp, author, notes, and initials
- Linked to specific incident for organization

## Implementation Files

- **Repository**: `app/repositories/bcp.py` - Database operations
- **Routes**: `app/api/routes/bcp.py` - API endpoints and page handlers
- **Template**: `app/templates/bcp/incident.html` - UI implementation
- **Tests**: `tests/test_bc06_incident_console.py` - Unit tests

## Usage

### Accessing the Incident Console
Navigate to `/bcp/incident` in the application.

### Starting an Incident
1. Click "Start Incident" button
2. Confirm the action
3. System creates incident record and initializes checklist
4. Distribution list receives portal alert (if configured)

### Managing Checklist
1. Navigate to Checklist tab
2. Click checkbox to mark items as complete
3. Completion timestamp and user are automatically recorded
4. Click again to unmark if needed

### Adding Contacts
1. Navigate to Contacts tab
2. Click "Add Contact" for Internal or External section
3. Fill in contact details
4. Save

### Logging Events
1. Navigate to Event Log tab
2. Click "Add Event"
3. Enter event details
4. Optionally specify timestamp (defaults to current time)
5. Save

### Closing an Incident
1. Click "Close Incident" button
2. Confirm the action
3. Incident status changes to Closed
4. System logs closure event

## Security Considerations

- Webhook endpoint requires API key authentication
- User permissions checked via `bcp:view` and `bcp:edit`
- All database operations use parameterized queries
- CSRF protection on state-changing requests
- Audit trail for all checklist and event log actions

## Future Enhancements

- Portal notification system for distribution list alerts
- Additional checklist phases (Crisis Recovery)
- Integration with more monitoring systems
- Automated incident reports
- Incident analytics and metrics
