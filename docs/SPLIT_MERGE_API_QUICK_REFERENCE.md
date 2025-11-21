# Quick Reference: Split and Merge Tickets API

## Authentication
All endpoints require helpdesk technician authentication via session cookie or bearer token.

## Split Ticket

### Endpoint
```
POST /api/tickets/{ticket_id}/split
```

### Request Example
```bash
curl -X POST "https://myportal.example.com/api/tickets/123/split" \
  -H "Content-Type: application/json" \
  -H "Cookie: session=..." \
  -d '{
    "reply_ids": [456, 457, 458],
    "new_subject": "Follow-up issue: Database performance"
  }'
```

### Response Example
```json
{
  "original_ticket": {
    "id": 123,
    "subject": "Server issues",
    "status": "open",
    ...
  },
  "new_ticket": {
    "id": 124,
    "subject": "Follow-up issue: Database performance",
    "status": "open",
    "split_from_ticket_id": 123,
    "company_id": 10,
    "requester_id": 20,
    ...
  },
  "moved_reply_count": 3
}
```

### Error Responses
- `400 Bad Request`: Invalid reply IDs or they don't belong to the ticket
- `404 Not Found`: Ticket not found
- `403 Forbidden`: Not a helpdesk technician

---

## Merge Tickets

### Endpoint
```
POST /api/tickets/merge
```

### Request Example
```bash
curl -X POST "https://myportal.example.com/api/tickets/merge" \
  -H "Content-Type: application/json" \
  -H "Cookie: session=..." \
  -d '{
    "ticket_ids": [123, 124, 125],
    "target_ticket_id": 123
  }'
```

### Response Example
```json
{
  "merged_ticket": {
    "id": 123,
    "subject": "Server issues",
    "status": "open",
    ...
  },
  "merged_ticket_ids": [124, 125],
  "moved_reply_count": 15,
  "moved_time_entry_count": 8
}
```

### Notes
- Tickets 124 and 125 will be closed and marked as merged into 123
- All replies and time entries moved to ticket 123
- Future replies to 124 or 125 will be routed to 123

### Error Responses
- `400 Bad Request`: Invalid ticket IDs, target not in list, or less than 2 tickets
- `404 Not Found`: One or more tickets not found
- `403 Forbidden`: Not a helpdesk technician

---

## Common Use Cases

### Case 1: Customer Reported Multiple Issues in One Ticket
1. Get the ticket details to see all replies
2. Identify reply IDs that belong to the second issue
3. Call split endpoint to create a new ticket for that issue

### Case 2: Customer Created Duplicate Tickets
1. Identify all duplicate ticket IDs
2. Choose which ticket should be the primary (usually oldest or most detailed)
3. Call merge endpoint with all ticket IDs and the target

### Case 3: Verifying Reply Routing
After merging tickets 100, 101 into 99:
- Any reply to ticket 100 or 101 automatically goes to ticket 99
- Check ticket 99 to see all conversation history
- Tickets 100 and 101 show status as "closed" with merge reference

---

## JavaScript Example (Frontend Integration)

```javascript
// Split ticket
async function splitTicket(ticketId, replyIds, newSubject) {
  const response = await fetch(`/api/tickets/${ticketId}/split`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    credentials: 'include', // Include session cookie
    body: JSON.stringify({
      reply_ids: replyIds,
      new_subject: newSubject,
    }),
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to split ticket');
  }
  
  return await response.json();
}

// Merge tickets
async function mergeTickets(ticketIds, targetTicketId) {
  const response = await fetch('/api/tickets/merge', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    credentials: 'include',
    body: JSON.stringify({
      ticket_ids: ticketIds,
      target_ticket_id: targetTicketId,
    }),
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to merge tickets');
  }
  
  return await response.json();
}

// Example usage
try {
  const result = await splitTicket(123, [456, 457], 'New ticket subject');
  console.log(`Created new ticket #${result.new_ticket.id}`);
  console.log(`Moved ${result.moved_reply_count} replies`);
} catch (error) {
  console.error('Split failed:', error.message);
}
```

---

## Python Example (Backend Integration)

```python
import httpx

async def split_ticket(
    ticket_id: int,
    reply_ids: list[int],
    new_subject: str,
    session_token: str
) -> dict:
    """Split a ticket via API."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://myportal.example.com/api/tickets/{ticket_id}/split",
            json={
                "reply_ids": reply_ids,
                "new_subject": new_subject,
            },
            cookies={"session": session_token},
        )
        response.raise_for_status()
        return response.json()

async def merge_tickets(
    ticket_ids: list[int],
    target_ticket_id: int,
    session_token: str
) -> dict:
    """Merge multiple tickets via API."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://myportal.example.com/api/tickets/merge",
            json={
                "ticket_ids": ticket_ids,
                "target_ticket_id": target_ticket_id,
            },
            cookies={"session": session_token},
        )
        response.raise_for_status()
        return response.json()

# Example usage
result = await split_ticket(
    ticket_id=123,
    reply_ids=[456, 457, 458],
    new_subject="Follow-up: Database issue",
    session_token="your-session-token"
)
print(f"Split successful! New ticket: {result['new_ticket']['id']}")
```

---

## Testing Endpoints

You can test the endpoints using the Swagger UI at `/docs` when the application is running.

1. Navigate to `https://myportal.example.com/docs`
2. Look for the "Tickets" section
3. Find the split and merge endpoints
4. Click "Try it out" to test interactively
