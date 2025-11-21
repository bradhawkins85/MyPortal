# Ticket Split and Merge Functionality

This document describes the split and merge ticket functionality implemented in MyPortal.

## Overview

MyPortal now supports splitting and merging tickets to help manage ticket workflows more effectively.

### Features

1. **Split Tickets**: Move selected conversation history items from an existing ticket to a new ticket
2. **Merge Tickets**: Combine multiple tickets into a single ticket
3. **Reply Routing**: Replies to merged tickets are automatically routed to the active merged ticket

## API Endpoints

### Split Ticket

**POST** `/api/tickets/{ticket_id}/split`

Splits a ticket by moving selected replies to a new ticket. The new ticket will have the same company and requester as the original ticket.

**Request Body:**
```json
{
  "reply_ids": [100, 101, 102],
  "new_subject": "Subject for the new ticket"
}
```

**Response:**
```json
{
  "original_ticket": { ... },
  "new_ticket": { ... },
  "moved_reply_count": 3
}
```

**Requirements:**
- Requires helpdesk technician permission
- All reply_ids must belong to the specified ticket
- At least one reply must be selected

### Merge Tickets

**POST** `/api/tickets/merge`

Merges multiple tickets into a single target ticket. All replies and time entries are moved to the target ticket, and source tickets are marked as closed and merged.

**Request Body:**
```json
{
  "ticket_ids": [1, 2, 3],
  "target_ticket_id": 1
}
```

**Response:**
```json
{
  "merged_ticket": { ... },
  "merged_ticket_ids": [2, 3],
  "moved_reply_count": 10,
  "moved_time_entry_count": 5
}
```

**Requirements:**
- Requires helpdesk technician permission
- At least 2 tickets must be provided
- Target ticket must be in the list of tickets being merged
- All tickets must exist

## Database Schema

The following columns were added to the `tickets` table:

- `merged_into_ticket_id` (INT, NULL): References the ticket this was merged into
- `split_from_ticket_id` (INT, NULL): References the ticket this was split from

## Reply Routing

When a reply is added to a ticket that has been merged into another ticket, the reply is automatically routed to the target merged ticket. This ensures that all conversation continues in the active ticket, even if users reply to old merged tickets.

The system follows the chain of `merged_into_ticket_id` references to find the final active ticket and protects against circular references.

## Usage Examples

### Example 1: Splitting a Ticket

A customer reports two separate issues in one ticket. A technician can split the ticket:

1. Identify the reply IDs that relate to the second issue
2. Call the split endpoint with those reply IDs and a new subject
3. The system creates a new ticket with the same company/requester
4. The selected replies are moved to the new ticket

### Example 2: Merging Duplicate Tickets

A customer accidentally creates multiple tickets for the same issue. A technician can merge them:

1. Identify all tickets that should be merged (e.g., tickets 100, 101, 102)
2. Choose which ticket should be the target (e.g., ticket 100)
3. Call the merge endpoint with the ticket IDs and target
4. The system moves all replies to ticket 100
5. Tickets 101 and 102 are marked as closed and merged into 100

### Example 3: Reply Routing After Merge

After tickets 101 and 102 are merged into ticket 100:

1. A customer replies to ticket 101 via email
2. The system checks if ticket 101 is merged (it is, into ticket 100)
3. The reply is automatically added to ticket 100 instead
4. The customer sees the conversation continue in the active ticket

## Testing

Comprehensive tests are available in `tests/test_ticket_split_merge.py` covering:

- Split ticket functionality
- Merge ticket functionality
- Reply routing through merged ticket chains
- Error handling for invalid inputs
- Circular reference protection

Run tests with:
```bash
pytest tests/test_ticket_split_merge.py -v
```

## Migration

The database migration is in `migrations/145_ticket_split_merge.sql` and will be applied automatically on application startup. The migration is idempotent and safe to run multiple times.

## Security

- Both split and merge operations require helpdesk technician permissions
- Validation ensures only authorized users can perform these operations
- Reply routing preserves security checks - users can only reply to tickets they have access to

## Future Enhancements

Potential future improvements could include:

- UI components in the ticket detail page for selecting replies and merging tickets
- Notification to users when their ticket is split or merged
- Bulk merge operations for handling multiple duplicate tickets
- History tracking to show when tickets were split or merged
- Ability to undo a merge operation
