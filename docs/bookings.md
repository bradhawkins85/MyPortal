# Cal.com Booking System

MyPortal now includes a Cal.com-style booking system that allows users to schedule meetings and manage their availability.

## Features

### Event Types

Event types define the type of meetings you offer with customizable settings:

- **Duration**: Set meeting lengths (15min, 30min, 1hr, custom)
- **Buffers**: Add time before/after meetings to prevent back-to-back scheduling
- **Minimum Notice**: Require advance booking (e.g., 24 hours minimum)
- **Location**: Configure meeting locations (Google Meet, Zoom, Phone, In-person)
- **Confirmation**: Require manual approval for bookings

### Availability Management

Define when you're available for meetings:

- **Schedules**: Create named schedules (e.g., "Work Hours", "Weekend Only")
- **Weekly Availability**: Set specific days and times for each day of the week
- **Date Overrides**: Block specific dates or add special availability
- **Timezone Support**: Automatically handle timezone conversions

### Bookings

Manage your scheduled meetings:

- **Create Bookings**: Accept bookings from attendees via API or UI
- **Reschedule**: Move bookings to different time slots
- **Cancel**: Cancel bookings with automatic notifications
- **Attendee Information**: Store attendee details and notes

### Webhooks

Get notified when booking events occur:

- **Event Types**: `BOOKING_CREATED`, `BOOKING_RESCHEDULED`, `BOOKING_CANCELLED`
- **Secure Delivery**: HMAC-SHA256 signature verification
- **Retry Logic**: Automatic retries with exponential backoff

## API Endpoints

### Bookings

#### Create a Booking

```bash
POST /v2/bookings
Content-Type: application/json

{
  "event_type_id": 1,
  "start": "2025-11-15T14:00:00Z",
  "attendee": {
    "name": "Jane Doe",
    "email": "jane@example.com",
    "timezone": "America/New_York",
    "notes": "Looking forward to the meeting"
  }
}
```

#### Get a Booking

```bash
GET /v2/bookings/{uid}
```

#### Reschedule a Booking

```bash
PATCH /v2/bookings/{uid}
Content-Type: application/json

{
  "start": "2025-11-16T15:00:00Z"
}
```

#### Cancel a Booking

```bash
DELETE /v2/bookings/{uid}
```

#### Get Available Slots

```bash
GET /v2/slots/available?event_type_id=1&start_time=2025-11-15T00:00:00Z&end_time=2025-11-22T23:59:59Z&timezone=America/New_York
```

### Event Types

#### List Event Types

```bash
GET /v2/event-types
```

#### Create Event Type

```bash
POST /v2/event-types
Content-Type: application/json
Authorization: Bearer {session_token}

{
  "title": "30 Minute Meeting",
  "slug": "30min",
  "duration_minutes": 30,
  "buffer_before_minutes": 5,
  "buffer_after_minutes": 5,
  "minimum_notice_hours": 24,
  "location_type": "google_meet",
  "is_active": true
}
```

#### Update Event Type

```bash
PATCH /v2/event-types/{id}
Content-Type: application/json
Authorization: Bearer {session_token}

{
  "is_active": false
}
```

#### Delete Event Type

```bash
DELETE /v2/event-types/{id}
Authorization: Bearer {session_token}
```

### Webhooks

#### List Webhooks

```bash
GET /v2/webhooks
Authorization: Bearer {session_token}
```

#### Create Webhook

```bash
POST /v2/webhooks
Content-Type: application/json
Authorization: Bearer {session_token}

{
  "subscriber_url": "https://yourapp.com/webhooks/bookings",
  "event_triggers": ["BOOKING_CREATED", "BOOKING_CANCELLED"],
  "active": true,
  "secret": "whsec_your_secret_here"
}
```

#### Update Webhook

```bash
PATCH /v2/webhooks/{id}
Content-Type: application/json
Authorization: Bearer {session_token}

{
  "active": false
}
```

#### Delete Webhook

```bash
DELETE /v2/webhooks/{id}
Authorization: Bearer {session_token}
```

## Admin Interface

Access the booking system admin interface at `/admin/bookings` (requires super admin privileges).

### Event Types Tab

- View all event types
- Create new event types
- Edit existing event types
- Toggle active/inactive status
- Delete event types

### Bookings Tab

- View all scheduled bookings
- See attendee information
- Check booking status
- Cancel bookings

### Webhooks Tab

- Manage webhook subscriptions (coming soon)

## Database Schema

The booking system uses the following database tables:

- `booking_event_types`: Event type definitions
- `booking_schedules`: Availability schedules
- `booking_availability`: Weekly availability rules
- `booking_date_overrides`: Special date availability
- `bookings`: Booking records
- `booking_attendees`: Attendee information
- `booking_webhooks`: Webhook subscriptions
- `booking_webhook_deliveries`: Webhook delivery log

## Example Workflow

### 1. Create an Event Type

```python
import httpx

async def create_event_type():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/v2/event-types",
            json={
                "title": "Coffee Chat",
                "slug": "coffee-chat",
                "duration_minutes": 30,
                "buffer_before_minutes": 5,
                "buffer_after_minutes": 5,
                "is_active": True,
            },
            cookies={"session": "your_session_cookie"}
        )
        return response.json()
```

### 2. Get Available Slots

```python
async def get_available_slots(event_type_id):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "http://localhost:8000/v2/slots/available",
            params={
                "event_type_id": event_type_id,
                "start_time": "2025-11-15T00:00:00Z",
                "end_time": "2025-11-22T23:59:59Z",
                "timezone": "America/New_York"
            }
        )
        return response.json()
```

### 3. Create a Booking

```python
async def create_booking(event_type_id, start_time):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/v2/bookings",
            json={
                "event_type_id": event_type_id,
                "start": start_time,
                "attendee": {
                    "name": "Jane Doe",
                    "email": "jane@example.com",
                    "timezone": "America/New_York"
                }
            }
        )
        return response.json()
```

## Webhook Signature Verification

Verify webhook signatures to ensure authenticity:

```python
import hmac
import hashlib

def verify_webhook_signature(payload: str, signature: str, secret: str) -> bool:
    expected_signature = hmac.new(
        secret.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected_signature)

# In your webhook handler
@app.post("/webhooks/bookings")
async def handle_booking_webhook(request: Request):
    payload = await request.body()
    signature = request.headers.get("x-cal-signature-256")
    secret = "whsec_your_secret_here"
    
    if not verify_webhook_signature(payload.decode(), signature, secret):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    data = await request.json()
    # Process webhook...
```

## Future Enhancements

- [ ] Team scheduling with round-robin assignment
- [ ] Recurring bookings
- [ ] Seated events (multiple attendees per slot)
- [ ] Calendar integrations (Google Calendar, Outlook)
- [ ] Public booking pages
- [ ] Email notifications
- [ ] SMS reminders
- [ ] Payment integration
- [ ] Custom branding

## Support

For questions or issues, please refer to the main MyPortal documentation or create an issue in the GitHub repository.
