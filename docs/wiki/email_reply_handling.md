# Email Reply Handling for Tickets

## Overview

Email replies received through IMAP are now intelligently matched to existing tickets instead of always creating new tickets. This document describes how the matching works and provides examples.

## How It Works

When an email is received via IMAP, the system attempts to find an existing ticket to append the reply to. The matching process follows these rules:

### 1. Ticket Number in Subject (Highest Priority)

If the email subject contains a ticket number, the system will find and append to that ticket.

**Supported patterns:**
- `#123` - Hash followed by number
- `Ticket: 456` - "Ticket:" followed by number
- `[#789]` - Hash and number in brackets
- `RE: Support Request #123` - With reply prefixes

**Examples:**
```
Original ticket subject: "Network issue in building A"
Ticket created as: #123

Reply email subjects that will match:
- "RE: Network issue in building A #123"
- "RE: RE: Network issue in building A #123"
- "FW: Network issue in building A [#123]"
- "Ticket: 123 - Follow-up"
```

### 2. Subject Matching (Fallback)

If no ticket number is found in the subject, the system will try to match by normalized subject. This only works if:
- The email sender is the ticket requester OR a watcher on the ticket
- The ticket is NOT closed or resolved
- The normalized subject matches an existing ticket's normalized subject

**Subject normalization removes:**
- Reply prefixes: RE:, FW:, FWD: (case insensitive, multiple levels)
- Tags: [External], [Important], etc.
- Ticket numbers: #123, Ticket: 123
- Extra whitespace

**Example:**
```
Original ticket: "Printer not working in office"
Created by: user@example.com

Email from user@example.com with subject "RE: Printer not working in office"
→ Matches and appends to original ticket

Email from different-user@example.com with same subject
→ Does NOT match (not requester or watcher)
```

### 3. Closed Tickets

**Important:** If an email references a closed ticket (either by ticket number or subject), a NEW ticket will be created instead of reopening the closed ticket.

**Example:**
```
Ticket #456 with subject "Database connection error" - Status: Closed

Email received: "RE: Database connection error #456"
→ Creates a NEW ticket (does not reopen #456)
```

## Code Implementation

### Key Functions

#### `_extract_ticket_number_from_subject(subject: str) -> str | None`
Extracts ticket numbers from email subjects using regex patterns.

#### `_normalize_subject_for_matching(subject: str) -> str`
Normalizes subjects by removing prefixes, tags, and ticket numbers for matching.

#### `_find_existing_ticket_for_reply(subject: str, from_email: str, requester_id: int | None) -> dict | None`
Main function that attempts to find an existing ticket for an email reply.

### Email Processing Flow

```python
# In sync_account() function:
1. Extract email subject and sender
2. Call _find_existing_ticket_for_reply()
   a. Try to extract ticket number from subject
   b. If found, look up ticket by ticket_number or id
   c. If ticket is closed, return None (no match)
   d. If no ticket number, try subject matching
   e. Only match non-closed tickets where sender is involved
3. If existing ticket found:
   - Add email as a reply to that ticket
   - Log the match
4. If no existing ticket:
   - Create new ticket
   - Add initial reply with email content
```

## Testing

The implementation includes comprehensive unit tests:

### Ticket Number Extraction Tests
- Basic hash pattern: `#123`
- With reply prefix: `RE: #123`
- Multiple prefixes: `RE: RE: #123`
- Ticket colon pattern: `Ticket: 123`
- Bracketed numbers: `[#123]`
- Multiple numbers (uses first): `#111 and #222`
- No number present
- Empty/None subjects

### Subject Normalization Tests
- Remove RE:, FW:, FWD: prefixes
- Multiple prefix levels
- Case insensitive removal
- External tags removal
- Ticket number removal
- Whitespace normalization
- Complex combinations

### Integration Tests
- Find ticket by number in subject
- Subject matching for non-closed tickets
- Closed tickets not matched
- No match when subject too short
- No match when sender not involved

## Examples

### Example 1: Reply with Ticket Number

**Initial Email:**
```
From: customer@company.com
Subject: Internet connection dropping
Body: Our internet keeps disconnecting every 5 minutes.
```

**Creates Ticket #101**

**Reply Email:**
```
From: customer@company.com
Subject: RE: Internet connection dropping #101
Body: This is still happening. Can you help?
```

**Result:** Reply added to ticket #101 (not a new ticket)

### Example 2: Reply Without Ticket Number (Subject Match)

**Initial Email:**
```
From: user@business.com
Subject: Email server not responding
Body: Cannot access email since this morning.
```

**Creates Ticket #202**

**Reply Email:**
```
From: user@business.com
Subject: RE: Email server not responding
Body: Still having the same problem.
```

**Result:** Reply added to ticket #202 (matched by subject and sender)

### Example 3: Closed Ticket (New Ticket Created)

**Original Ticket #303:** Status: Closed
Subject: "Laptop keyboard issue"

**New Email:**
```
From: customer@company.com
Subject: RE: Laptop keyboard issue #303
Body: The problem has returned.
```

**Result:** Creates NEW ticket #304 (does not reopen #303)

### Example 4: No Match (Different Sender)

**Original Ticket #404:**
Created by: john@company.com
Subject: "VPN configuration help"

**Email:**
```
From: jane@company.com
Subject: RE: VPN configuration help
Body: I need help too.
```

**Result:** Creates NEW ticket #405 (jane is not requester or watcher on #404)

## Configuration

No special configuration is required. The feature works automatically for all IMAP accounts configured in the system.

## Logging

The system logs important events:

```
Email matched to existing ticket
  account_id: 1
  uid: 12345
  ticket_id: 101
  subject: RE: Internet connection dropping #101

Added email reply to existing ticket
  account_id: 1
  uid: 12345
  ticket_id: 101
```

## Database Schema

No database changes were required. The implementation uses existing tables:
- `tickets` - Main ticket records
- `ticket_replies` - Conversation entries
- `ticket_watchers` - Users watching tickets
- `users` - For email matching

## Performance Considerations

- Ticket number lookup uses indexed queries (by ticket_number or id)
- Subject matching limits results to 20 most recent tickets
- Query filters by status to exclude closed tickets
- Query filters by requester/watcher for security

## Future Enhancements

Potential improvements:
1. Add threading support using Email Message-ID headers
2. Support for In-Reply-To and References headers
3. Configurable closed status handling per account
4. Machine learning for better subject matching
5. Support for multiple ticket numbers in one email
