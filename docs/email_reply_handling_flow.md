# Email Reply Handling - Visual Flow Diagram

## Scenario 1: Email with Ticket Number

```
┌─────────────────────────────────────────┐
│ Email Received via IMAP                 │
│                                         │
│ From: customer@company.com              │
│ Subject: RE: Network Issue #123         │
│ Body: Still having the problem...       │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Extract ticket number from subject      │
│ Pattern: #123                           │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Look up ticket #123                     │
│ Found: Yes                              │
│ Status: Open                            │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ ✅ Add reply to existing ticket #123    │
│                                         │
│ Author: customer@company.com (requester)│
│ Body: Still having the problem...       │
│ Created: <timestamp>                    │
└─────────────────────────────────────────┘
```

## Scenario 2: Email without Ticket Number (Subject Match)

```
┌─────────────────────────────────────────┐
│ Email Received via IMAP                 │
│                                         │
│ From: customer@company.com              │
│ Subject: RE: Network Issue              │
│ Body: Follow up on previous email...    │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Extract ticket number from subject      │
│ Result: None (no number found)          │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Normalize subject for matching          │
│ Input:  "RE: Network Issue"             │
│ Output: "Network Issue"                 │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Search for tickets with matching subject│
│ - Status: NOT closed/resolved           │
│ - Sender: requester or watcher          │
│ Found: Ticket #124 "Network Issue"      │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ ✅ Add reply to existing ticket #124    │
│                                         │
│ Author: customer@company.com (requester)│
│ Body: Follow up on previous email...    │
└─────────────────────────────────────────┘
```

## Scenario 3: Reply to Closed Ticket (New Ticket Created)

```
┌─────────────────────────────────────────┐
│ Email Received via IMAP                 │
│                                         │
│ From: customer@company.com              │
│ Subject: RE: Resolved Issue #125        │
│ Body: Problem is back again...          │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Extract ticket number from subject      │
│ Pattern: #125                           │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Look up ticket #125                     │
│ Found: Yes                              │
│ Status: Closed ⚠️                       │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Ticket is closed - do not match         │
│ Return: None                            │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ ✅ Create NEW ticket #126                │
│                                         │
│ Subject: RE: Resolved Issue #125        │
│ Requester: customer@company.com         │
│ Body: Problem is back again...          │
└─────────────────────────────────────────┘
```

## Scenario 4: No Match (Different Sender)

```
┌─────────────────────────────────────────┐
│ Email Received via IMAP                 │
│                                         │
│ From: other-user@different.com          │
│ Subject: RE: Network Issue              │
│ Body: I have the same problem...        │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Extract ticket number from subject      │
│ Result: None                            │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Normalize subject: "Network Issue"      │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Search for tickets with matching subject│
│ - Status: NOT closed/resolved           │
│ - Sender: requester or watcher ⚠️       │
│ Found: Ticket #124 exists but...        │
│ - Requester: customer@company.com       │
│ - Watchers: []                          │
│ Sender NOT authorized - no match        │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ ✅ Create NEW ticket #127                │
│                                         │
│ Subject: RE: Network Issue              │
│ Requester: other-user@different.com     │
│ Body: I have the same problem...        │
└─────────────────────────────────────────┘
```

## Decision Flow Chart

```
                    ┌─────────────────┐
                    │ Email Received  │
                    └────────┬────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │ Ticket # in subject? │
                  └─────┬───────────┬────┘
                   YES  │           │ NO
                        ▼           ▼
             ┌──────────────┐  ┌────────────────┐
             │ Find by #    │  │ Normalize      │
             │              │  │ subject        │
             └──────┬───────┘  └────────┬───────┘
                    │                   │
                    ▼                   ▼
          ┌─────────────────┐  ┌────────────────────┐
          │ Ticket found?   │  │ Find by subject    │
          └─────┬──────┬────┘  │ + sender match     │
           YES  │      │ NO    └────────┬───────────┘
                │      │                │
                ▼      │                ▼
    ┌──────────────┐  │      ┌─────────────────┐
    │ Closed?      │  │      │ Ticket found?   │
    └──┬────────┬──┘  │      └─────┬──────┬────┘
  YES  │        │ NO  │       YES  │      │ NO
       │        │     │            │      │
       ▼        ▼     │            ▼      ▼
  ┌─────────────────┐│       ┌──────────────────┐
  │ Create NEW      ││       │ Add reply to     │
  │ ticket          ││       │ existing ticket  │
  └─────────────────┘│       └──────────────────┘
                     │
                     └─────────────────┐
                                       │
                                       ▼
                              ┌─────────────────┐
                              │ Create NEW      │
                              │ ticket          │
                              └─────────────────┘
```

## Key Security Checks

```
┌─────────────────────────────────────────┐
│ Subject-Based Matching Security         │
├─────────────────────────────────────────┤
│                                         │
│ ✓ Sender must be requester OR watcher  │
│ ✓ Ticket must NOT be closed/resolved   │
│ ✓ Subject must be >5 chars normalized  │
│ ✓ Exact match on normalized subjects   │
│                                         │
│ This prevents:                          │
│ • Unauthorized users replying           │
│ • Reopening closed tickets              │
│ • Accidental matches on short subjects  │
│ • Cross-company ticket access           │
│                                         │
└─────────────────────────────────────────┘
```

## Code Locations

```
File: app/services/imap.py

┌─────────────────────────────────────────┐
│ Helper Functions                        │
├─────────────────────────────────────────┤
│ _extract_ticket_number_from_subject()   │
│   Line: ~850                            │
│   Purpose: Extract #123 from subjects   │
│                                         │
│ _normalize_subject_for_matching()       │
│   Line: ~870                            │
│   Purpose: Remove RE:, FW:, #numbers    │
│                                         │
│ _find_existing_ticket_for_reply()       │
│   Line: ~920                            │
│   Purpose: Find existing ticket         │
│   - By ticket number (priority)         │
│   - By subject (fallback)               │
│   - Security checks                     │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│ Main Logic                              │
├─────────────────────────────────────────┤
│ sync_account()                          │
│   Line: ~1310                           │
│   Updated section:                      │
│   - Call _find_existing_ticket_for_reply│
│   - If found: add reply                 │
│   - If not found: create new ticket     │
└─────────────────────────────────────────┘
```
