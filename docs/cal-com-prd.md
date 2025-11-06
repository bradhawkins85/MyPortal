# Cal.com Product Requirements Document (PRD)

**Version:** 1.0  
**Date:** November 6, 2025  
**Status:** Parity & Capability Analysis

---

## 1. Overview & Goals

### What is Cal.com?

Cal.com is an open-source scheduling infrastructure that provides flexible, developer-friendly booking capabilities for individuals, teams, and enterprises. It serves as a comprehensive calendar and scheduling platform that can be self-hosted or used as a cloud service, offering deep customization and extensive integration options.

### Target Audiences

| Persona | Description |
|---------|-------------|
| **Individuals** | Freelancers, consultants, and professionals who need simple booking pages |
| **Teams** | Small to medium-sized teams requiring collaborative scheduling |
| **Enterprises** | Large organizations needing compliance, SSO, and advanced routing |
| **Developers** | Engineering teams building custom scheduling workflows via API |

### Top-Level Goals

- **Fast, Reliable Bookings**: Sub-second slot search and booking confirmation with < 1s API response time (p50)
- **Deep Customization**: White-label UI, custom workflows, conditional routing, and event type flexibility
- **Developer-Friendly Platform**: Comprehensive REST APIs, webhooks, embeddable React components ("Atoms"), and modern SDK patterns
- **Enterprise Ready**: SOC 2, HIPAA, ISO 27001 compliance, SSO (SAML/OIDC), on-premises deployment options

---

## 2. Core Concepts & User Personas

### User Personas

| Persona | Primary Activities | Key Needs |
|---------|-------------------|-----------|
| **Invitee/Guest** | Browse availability, book time slots, reschedule/cancel meetings | Simple UX, timezone clarity, confirmation emails |
| **Host** | Define event types, set availability, manage bookings | Flexible schedules, calendar sync, automated reminders |
| **Team Admin** | Configure team scheduling, routing rules, collective calendars | Round-robin assignment, priority weighting, analytics |
| **Org Admin** | Enforce policies, manage SSO, oversee compliance | Audit logs, role-based access, security controls |
| **Developer/Integrator** | Embed scheduling, build automations, consume APIs | Clear docs, webhook reliability, SDK availability |
| **Compliance/Security** | Ensure data protection, audit access, manage incidents | GDPR compliance, data residency, encryption at rest/transit |

### Key Entities

#### Event Types

- **Regular Events**: One-time or recurring meetings with fixed duration
- **Recurring Events**: Series-based bookings (daily, weekly, monthly patterns)
- **Instant Events**: On-demand bookings without confirmation delay
- **Seated Events**: Multiple attendees booking the same time slot
- **Collective Events**: Team members appear together in a single booking

#### Schedules & Availability

- **Schedules**: Named time blocks defining when a host is available (e.g., "Work Hours", "Weekends Only")
- **Availabilities**: Granular rules within schedules (specific days/times, buffers, breaks)
- **Overrides**: One-off exceptions to regular availability (e.g., vacation, special hours)

#### Attendees & Teams

- **Attendees**: Invitees who book time slots
- **Teams/Collections**: Groups of hosts sharing event types and routing logic
- **Round-Robin**: Automatic assignment of bookings across team members

#### Workflows & Automations

- **Workflows**: Conditional logic triggered by booking events (send SMS, update CRM, etc.)
- **Webhooks**: External system notifications on booking lifecycle events
- **Routing Forms**: Multi-step forms that conditionally route to different event types or team members

---

## 3. Functional Requirements (Parity Scope)

### 3.1 Booking Flows

| Feature | Description | API Support |
|---------|-------------|-------------|
| **Create Booking** | Invitee selects available slot, provides details, confirms | `POST /v2/bookings` |
| **Reschedule Booking** | Change existing booking to new time slot | `PATCH /v2/bookings/{uid}` |
| **Cancel Booking** | Host or invitee cancels confirmed booking | `DELETE /v2/bookings/{uid}` |
| **Confirm Booking** | Two-step confirmation for manual approval workflows | `POST /v2/bookings/{uid}/confirm` |
| **Instant Bookings** | Skip confirmation step, immediately reserve time | Event type config flag |
| **Recurring Bookings** | Book multiple instances in a series | Recurring event type |
| **Seated Bookings** | Multiple attendees in same slot (webinars, classes) | Seats config on event type |

**Example: Create Booking Request**

```json
{
  "eventTypeId": 123456,
  "start": "2025-11-15T14:00:00Z",
  "attendee": {
    "name": "Jane Doe",
    "email": "jane@example.com",
    "timeZone": "America/New_York",
    "notes": "Looking forward to discussing the project"
  },
  "metadata": {
    "customField1": "value1"
  }
}
```

**Example: Create Booking Response**

```json
{
  "status": "success",
  "data": {
    "uid": "bk_abc123def456",
    "id": 789012,
    "eventTypeId": 123456,
    "title": "30 Minute Meeting",
    "start": "2025-11-15T14:00:00Z",
    "end": "2025-11-15T14:30:00Z",
    "status": "accepted",
    "attendees": [
      {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "timeZone": "America/New_York"
      }
    ],
    "location": "https://meet.google.com/abc-defg-hij",
    "metadata": {
      "customField1": "value1"
    }
  }
}
```

### 3.2 Event Types & Availability

| Feature | Description |
|---------|-------------|
| **Duration Management** | Set fixed durations (15min, 30min, 1hr) or variable lengths |
| **Buffer Time** | Pre/post-meeting buffers to prevent back-to-back bookings |
| **Timezone Handling** | Display slots in invitee timezone, convert to host timezone |
| **Recurring Patterns** | Daily, weekly, bi-weekly, monthly patterns with end dates |
| **Availability CRUD** | Create, read, update, delete availability rules via API |
| **Date Overrides** | One-off availability changes (holidays, special events) |
| **Minimum Notice** | Require X hours/days advance booking |
| **Date Range Limits** | Restrict how far in advance bookings can be made |

**Availability Priority Logic**:
1. Date-specific overrides (highest priority)
2. Schedule-specific rules
3. Default availability (lowest priority)

### 3.3 Team Scheduling

| Feature | Description | Configuration |
|---------|-------------|---------------|
| **Round-Robin** | Distribute bookings evenly across team members | Equal weighting by default |
| **Priority Weighting** | Assign more bookings to specific team members | Weight multipliers (e.g., 2x) |
| **Least-Recently-Booked** | Favor team members who haven't had recent bookings | Timestamp-based sorting |
| **Collective Calendars** | All team members attend same meeting | Team event type config |
| **Pooled Availability** | Combine individual schedules into unified view | Merge algorithm |
| **Team-Specific Routing** | Route based on skills, departments, regions | Routing form integration |

**Round-Robin Assignment Example**:
```
Team: Sales (3 members)
- Alice (weight: 1, last booking: 2 days ago)
- Bob (weight: 2, last booking: 5 hours ago)  
- Carol (weight: 1, last booking: 1 week ago)

Next booking assignment: Carol (least recent, equal weight)
After Carol: Alice (next least recent)
After Alice: Bob (gets 2x bookings due to weight)
```

### 3.4 Routing Forms

Routing Forms provide conditional logic to direct invitees to appropriate event types or team members based on their responses.

| Feature | Description |
|---------|-------------|
| **Form Builder** | Drag-and-drop interface for creating multi-step forms |
| **Field Types** | Text, select, multi-select, radio, checkbox, email, phone |
| **Conditional Logic** | If/then rules based on form responses |
| **Event Type Routing** | Route to different event types based on answers |
| **Team Member Routing** | Assign to specific hosts based on criteria |
| **CRM-Aware Routing** | Match to account owner in connected CRM |
| **Pre-Fill Support** | Auto-populate fields from URL parameters |

**Example Routing Scenario**:
```
Form Question: "What type of support do you need?"
- Sales Inquiry → Route to "Sales Demo" event type
- Technical Support → Route to "Technical Support" team (round-robin)
- Billing Question → Route to "Billing Team" with priority to account owner
```

### 3.5 Conferencing & Locations

| Provider | Integration Type | Auto-Join Links |
|----------|------------------|-----------------|
| **Zoom** | OAuth connection | Yes |
| **Google Meet** | OAuth via Google Calendar | Yes |
| **Microsoft Teams** | OAuth via Office 365 | Yes |
| **Phone** | Manual entry of phone number | N/A |
| **In-Person** | Custom address field | N/A |
| **Custom URL** | Arbitrary meeting link | Optional |

**Location Selection**:
- **Host Default**: Pre-configured location for event type
- **Invitee Choice**: Allow invitee to select from options (e.g., phone vs. video)
- **Conditional**: Different locations based on routing form responses

### 3.6 Embedding & UI Components

#### Modern React "Atoms" (Booker Component)

Cal.com provides embeddable React components called "Atoms" for modern integration.

**Example: Booker Atom Usage**

```tsx
import Cal, { getCalApi } from "@calcom/embed-react";
import { useEffect } from "react";

export default function MyBookingPage() {
  useEffect(() => {
    (async function () {
      const cal = await getCalApi();
      cal("ui", {
        theme: "light",
        styles: { branding: { brandColor: "#0066FF" } },
        hideEventTypeDetails: false
      });
    })();
  }, []);

  return (
    <Cal
      calLink="john-doe/30min"
      style={{ width: "100%", height: "100%", overflow: "scroll" }}
      config={{
        layout: "month_view",
        theme: "light"
      }}
    />
  );
}
```

#### Embedding Options

| Embed Type | Use Case | Implementation |
|------------|----------|----------------|
| **Inline** | Full-page scheduling widget | `<iframe>` or React component |
| **Pop-up** | Modal overlay on button click | JavaScript snippet |
| **Button** | Floating action button | Pre-styled component |
| **Custom** | Headless integration via API | Custom UI + API calls |

**Example: Pop-up Embed**

```html
<!-- Button trigger -->
<button data-cal-link="john-doe/30min" data-cal-config='{"layout":"month_view"}'>
  Schedule a Meeting
</button>

<!-- Cal.com embed script -->
<script type="text/javascript">
  (function (C, A, L) {
    let p = function (a, ar) {
      a.q.push(ar);
    };
    let d = C.document;
    C.Cal = C.Cal || function () {
      let cal = C.Cal;
      let ar = arguments;
      if (!cal.loaded) {
        cal.ns = {};
        cal.q = cal.q || [];
        d.head.appendChild(d.createElement("script")).src = A;
        cal.loaded = true;
      }
      if (ar[0] === L) {
        const api = function () {
          p(api, arguments);
        };
        const namespace = ar[1];
        api.q = api.q || [];
        typeof namespace === "string" ? (cal.ns[namespace] = api) && p(api, ar) : p(cal, ar);
        return;
      }
      p(cal, ar);
    };
  })(window, "https://app.cal.com/embed/embed.js", "init");

  Cal("init", { origin: "https://app.cal.com" });
  Cal("ui", {
    theme: "light",
    styles: { branding: { brandColor: "#0066FF" } },
  });
</script>
```

#### Theming & White-Label

- **Brand Colors**: Custom primary/secondary colors
- **Logo Replacement**: Custom logo in booking interface
- **Domain Masking**: Serve from custom domain (e.g., `cal.yourcompany.com`)
- **CSS Overrides**: Custom stylesheets for advanced branding
- **Email Templates**: Branded confirmation/reminder emails

### 3.7 Integrations

#### Calendar Providers

Cal.com uses a "unified calendar" approach to abstract provider-specific differences.

| Provider | Auth Method | Sync Type | Conflict Detection |
|----------|-------------|-----------|-------------------|
| **Google Calendar** | OAuth 2.0 | Bidirectional | Yes |
| **Outlook/Microsoft 365** | OAuth 2.0 | Bidirectional | Yes |
| **Apple Calendar** | CalDAV | Bidirectional | Limited |
| **iCloud** | CalDAV | Bidirectional | Limited |

**Unified Calendar Concept**:
- Single API interface for all providers
- Automatic conflict checking across multiple calendars
- Transparent sync for new bookings, updates, cancellations
- Provider-agnostic availability calculation

#### CRM & Business Tools

- **Salesforce**: Sync contacts, create opportunities
- **HubSpot**: Log meetings, update contact properties
- **Pipedrive**: Create activities, link to deals
- **Zapier/Make**: Connect to 5,000+ apps via webhooks

### 3.8 API

#### Authentication

Cal.com API v2 uses API keys for authentication.

**API Key Generation**:
1. Navigate to Settings → Security → API Keys
2. Click "Generate New API Key"
3. Store securely (shown only once): `cal_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

**Never expose API keys in client-side code or public repositories.**

**Example API Request with Authentication**:

```bash
curl -X POST https://api.cal.com/v2/bookings \
  -H "Authorization: Bearer cal_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "eventTypeId": 123456,
    "start": "2025-11-15T14:00:00Z",
    "attendee": {
      "name": "Jane Doe",
      "email": "jane@example.com",
      "timeZone": "America/New_York"
    }
  }'
```

#### Key Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /v2/bookings` | Create | Create a new booking |
| `GET /v2/bookings/{uid}` | Read | Retrieve booking by UID |
| `PATCH /v2/bookings/{uid}` | Update | Reschedule booking |
| `DELETE /v2/bookings/{uid}` | Delete | Cancel booking |
| `GET /v2/slots/available` | Read | Get available time slots |
| `GET /v1/availability` | Read | Legacy availability endpoint |
| `POST /v2/webhooks` | Create | Register webhook subscription |
| `GET /v2/webhooks` | Read | List webhook subscriptions |

**Example: Get Booking by UID**

```bash
curl -X GET https://api.cal.com/v2/bookings/bk_abc123def456 \
  -H "Authorization: Bearer cal_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

**Response**:

```json
{
  "status": "success",
  "data": {
    "uid": "bk_abc123def456",
    "id": 789012,
    "eventTypeId": 123456,
    "title": "30 Minute Meeting",
    "start": "2025-11-15T14:00:00Z",
    "end": "2025-11-15T14:30:00Z",
    "status": "accepted",
    "attendees": [
      {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "timeZone": "America/New_York"
      }
    ],
    "host": {
      "id": 42,
      "name": "John Smith",
      "email": "john@example.com"
    },
    "location": "https://meet.google.com/abc-defg-hij",
    "metadata": {
      "customField1": "value1"
    },
    "createdAt": "2025-11-10T09:23:45Z",
    "updatedAt": "2025-11-10T09:23:45Z"
  }
}
```

### 3.9 Webhooks & Automations

Webhooks enable real-time notifications for booking lifecycle events.

#### Webhook Triggers

| Trigger | Event Name | Description |
|---------|-----------|-------------|
| Booking Created | `BOOKING_CREATED` | New booking confirmed |
| Booking Rescheduled | `BOOKING_RESCHEDULED` | Booking moved to new time |
| Booking Cancelled | `BOOKING_CANCELLED` | Booking cancelled by host/invitee |
| Booking Paid | `BOOKING_PAID` | Payment received for booking |
| Booking No-Show | `BOOKING_NO_SHOW` | Attendee marked as no-show |
| Meeting Started | `MEETING_STARTED` | Meeting begins (conferencing integration) |
| Meeting Ended | `MEETING_ENDED` | Meeting concludes |
| Recording Ready | `RECORDING_READY` | Meeting recording available |
| Form Submitted | `FORM_SUBMITTED` | Routing form completed |
| OOO Created | `OOO_CREATED` | Out-of-office override added |

#### Webhook Configuration

**Example: Create Webhook Subscription**

```bash
curl -X POST https://api.cal.com/v2/webhooks \
  -H "Authorization: Bearer cal_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "subscriberUrl": "https://yourapp.com/webhooks/cal",
    "eventTriggers": ["BOOKING_CREATED", "BOOKING_RESCHEDULED", "BOOKING_CANCELLED"],
    "active": true,
    "payloadTemplate": null,
    "secret": "whsec_your_webhook_secret_here"
  }'
```

#### Webhook Payload Example

**Event: BOOKING_CREATED**

```json
{
  "triggerEvent": "BOOKING_CREATED",
  "createdAt": "2025-11-15T14:05:23Z",
  "payload": {
    "uid": "bk_abc123def456",
    "id": 789012,
    "eventTypeId": 123456,
    "title": "30 Minute Meeting",
    "startTime": "2025-11-15T14:00:00Z",
    "endTime": "2025-11-15T14:30:00Z",
    "organizer": {
      "id": 42,
      "name": "John Smith",
      "email": "john@example.com",
      "timeZone": "America/Los_Angeles"
    },
    "attendees": [
      {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "timeZone": "America/New_York"
      }
    ],
    "location": "https://meet.google.com/abc-defg-hij",
    "metadata": {
      "customField1": "value1"
    },
    "responses": {
      "name": "Jane Doe",
      "email": "jane@example.com",
      "guests": [],
      "notes": "Looking forward to discussing the project"
    }
  }
}
```

#### Webhook Security

**Secret Signing**:
- Each webhook includes an `X-Cal-Signature-256` header
- Signature is HMAC-SHA256 of request body using shared secret
- Verify signature to ensure authenticity

**Example: Verify Webhook Signature (Node.js)**

```javascript
const crypto = require('crypto');

function verifyWebhook(payload, signature, secret) {
  const expectedSignature = crypto
    .createHmac('sha256', secret)
    .update(payload)
    .digest('hex');
  
  return crypto.timingSafeEqual(
    Buffer.from(signature),
    Buffer.from(expectedSignature)
  );
}

// In your webhook handler
app.post('/webhooks/cal', (req, res) => {
  const signature = req.headers['x-cal-signature-256'];
  const secret = process.env.CAL_WEBHOOK_SECRET; // whsec_...
  
  if (!verifyWebhook(JSON.stringify(req.body), signature, secret)) {
    return res.status(401).send('Invalid signature');
  }
  
  // Process webhook
  const { triggerEvent, payload } = req.body;
  console.log(`Received ${triggerEvent} for booking ${payload.uid}`);
  
  res.status(200).send('OK');
});
```

#### Payload Templates

Customize webhook payloads using template variables:

```json
{
  "eventName": "{{triggerEvent}}",
  "bookingId": "{{payload.uid}}",
  "customerEmail": "{{payload.attendees[0].email}}",
  "meetingStart": "{{payload.startTime}}",
  "customMessage": "Meeting scheduled with {{payload.organizer.name}}"
}
```

### 3.10 Security & Identity

#### Single Sign-On (SSO)

| Protocol | Use Case | Configuration |
|----------|----------|---------------|
| **SAML 2.0** | Enterprise identity providers (Okta, Azure AD) | Upload IdP metadata XML |
| **OIDC** | Modern OAuth-based SSO (Google, Auth0) | Configure client ID/secret |

**SAML Configuration Steps**:
1. Navigate to Settings → Security → SSO
2. Select "SAML 2.0"
3. Upload IdP metadata XML or enter manually:
   - Entity ID
   - SSO URL
   - Certificate
4. Configure attribute mapping (email, name, groups)
5. Test connection with sample user
6. Enable for organization

#### Roles & Permissions

| Role | Permissions | Scope |
|------|-------------|-------|
| **Owner** | Full administrative access | Organization-wide |
| **Admin** | Manage users, teams, settings | Organization-wide |
| **Member** | Create event types, manage own bookings | Personal scope |
| **Guest** | Book meetings only | No admin access |

**Team-Level Permissions**:
- Manage team event types
- View team analytics
- Manage team members
- Configure routing rules

### 3.11 Enterprise Features

#### Compliance Certifications

| Standard | Description | Availability |
|----------|-------------|--------------|
| **SOC 2 Type II** | Security, availability, confidentiality controls | Enterprise plan |
| **HIPAA** | Healthcare data protection | Enterprise plan + BAA |
| **ISO 27001** | Information security management | Enterprise plan |
| **GDPR** | EU data protection compliance | All plans |

#### Deployment Options

| Option | Description | SLA |
|--------|-------------|-----|
| **Cloud (Multi-tenant)** | Shared infrastructure, managed by Cal.com | 99.9% uptime |
| **Dedicated Cloud** | Isolated infrastructure, managed by Cal.com | 99.95% uptime |
| **On-Premises** | Self-hosted, customer-managed | Customer-defined |

#### White-Label Enterprise

- Custom domain (e.g., `meetings.yourcompany.com`)
- Remove all Cal.com branding
- Custom email sender domain
- Branded mobile apps (additional cost)

---

## 4. Non-Functional Requirements

### 4.1 Performance

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Slot Search Response Time** | < 1s p50, < 2s p95 | API response time for `GET /v2/slots/available` |
| **Booking Creation** | < 500ms p50, < 1s p95 | API response time for `POST /v2/bookings` |
| **Page Load Time** | < 2s p50 for booking page | Lighthouse/WebPageTest |
| **Webhook Delivery** | < 5s p95 | Time from event to webhook POST |

### 4.2 Reliability & SLAs

| Plan | Uptime SLA | Support Response Time |
|------|------------|---------------------|
| **Free** | Best effort | Community forum |
| **Pro** | 99.9% | 24 hours |
| **Enterprise** | 99.95% | 4 hours (critical), 24 hours (normal) |

**Downtime Compensation**:
- Pro: 10% monthly credit per 0.1% below SLA
- Enterprise: Custom contractual terms

### 4.3 Rate Limits

| Endpoint | Rate Limit | Burst Allowance |
|----------|-----------|-----------------|
| `POST /v2/bookings` | 100 req/min | 150 req/min (1 min) |
| `GET /v2/slots/available` | 300 req/min | 500 req/min (1 min) |
| `GET /v2/bookings/{uid}` | 1000 req/min | 1500 req/min (1 min) |
| `POST /v2/webhooks` | 10 req/min | 20 req/min (1 min) |

**Rate Limit Headers**:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1699564800
```

### 4.4 Pagination

All list endpoints support cursor-based pagination:

```bash
GET /v2/bookings?limit=50&cursor=eyJpZCI6MTIzNDU2fQ==
```

**Response**:
```json
{
  "status": "success",
  "data": [...],
  "pagination": {
    "cursor": "eyJpZCI6MTIzNTA2fQ==",
    "hasMore": true,
    "limit": 50
  }
}
```

### 4.5 Idempotency

Mutation endpoints (`POST`, `PATCH`, `DELETE`) support idempotency via `Idempotency-Key` header:

```bash
curl -X POST https://api.cal.com/v2/bookings \
  -H "Authorization: Bearer cal_live_xxx" \
  -H "Idempotency-Key: unique-key-12345" \
  -H "Content-Type: application/json" \
  -d '{...}'
```

Repeated requests with same key return original response (24-hour retention).

### 4.6 Audit Logs

Enterprise plans include comprehensive audit logs:

- User login/logout events
- Booking creation/modification/cancellation
- Event type configuration changes
- Team membership changes
- API key generation/revocation
- SSO configuration updates

**Retention**: 1 year (configurable up to 7 years)

**Export**: JSON, CSV formats via API or dashboard

### 4.7 Privacy & GDPR

| Feature | Description |
|---------|-------------|
| **Data Export** | Users can download all their data (bookings, event types, etc.) |
| **Data Deletion** | Right to be forgotten: delete all user data permanently |
| **Consent Management** | Track and manage user consent for data processing |
| **Cookie Controls** | Granular control over analytics/tracking cookies |
| **Data Processing Agreement** | Available for enterprise customers |

### 4.8 Data Residency

Enterprise customers can specify data storage region:

- **US** (default): AWS us-east-1
- **EU**: AWS eu-west-1 (Frankfurt)
- **UK**: AWS eu-west-2 (London)
- **APAC**: AWS ap-southeast-1 (Singapore)

---

## 5. Developer Experience

### 5.1 API Authentication Patterns

**API Key (Recommended)**:
```bash
curl -H "Authorization: Bearer cal_live_xxx" https://api.cal.com/v2/bookings
```

**OAuth 2.0 (For User Context)**:
1. Redirect user to: `https://app.cal.com/oauth/authorize?client_id=xxx&redirect_uri=xxx`
2. Exchange code for token: `POST /oauth/token`
3. Use access token: `Authorization: Bearer {access_token}`

### 5.2 Example Requests

#### Create Booking

```bash
curl -X POST https://api.cal.com/v2/bookings \
  -H "Authorization: Bearer cal_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "eventTypeId": 123456,
    "start": "2025-11-20T15:00:00Z",
    "attendee": {
      "name": "Alice Johnson",
      "email": "alice@example.com",
      "timeZone": "Europe/London"
    },
    "metadata": {
      "source": "mobile_app",
      "campaign": "Q4_2025"
    }
  }'
```

#### Get Available Slots

```bash
curl -X GET "https://api.cal.com/v2/slots/available?eventTypeId=123456&startTime=2025-11-20T00:00:00Z&endTime=2025-11-27T23:59:59Z&timeZone=America/New_York" \
  -H "Authorization: Bearer cal_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

**Response**:
```json
{
  "status": "success",
  "data": {
    "slots": {
      "2025-11-20": ["09:00:00", "10:00:00", "11:00:00", "14:00:00", "15:00:00"],
      "2025-11-21": ["09:00:00", "09:30:00", "10:00:00", "10:30:00", "11:00:00"],
      "2025-11-22": []
    }
  }
}
```

### 5.3 Webhook Examples

**Webhook Subscription**:

```bash
curl -X POST https://api.cal.com/v2/webhooks \
  -H "Authorization: Bearer cal_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "subscriberUrl": "https://api.yourapp.com/webhooks/cal",
    "eventTriggers": ["BOOKING_CREATED", "BOOKING_CANCELLED"],
    "active": true,
    "secret": "whsec_your_strong_secret_here"
  }'
```

**Webhook Handler (Express.js)**:

```javascript
const express = require('express');
const crypto = require('crypto');

const app = express();
app.use(express.json());

app.post('/webhooks/cal', (req, res) => {
  // Verify signature
  const signature = req.headers['x-cal-signature-256'];
  const secret = process.env.CAL_WEBHOOK_SECRET;
  const body = JSON.stringify(req.body);
  
  const expectedSig = crypto
    .createHmac('sha256', secret)
    .update(body)
    .digest('hex');
  
  if (signature !== expectedSig) {
    return res.status(401).send('Invalid signature');
  }
  
  // Handle event
  const { triggerEvent, payload } = req.body;
  
  switch (triggerEvent) {
    case 'BOOKING_CREATED':
      console.log(`New booking: ${payload.uid}`);
      // Send to CRM, notify team, etc.
      break;
    case 'BOOKING_CANCELLED':
      console.log(`Cancelled booking: ${payload.uid}`);
      // Update records, send notification, etc.
      break;
  }
  
  res.status(200).send('OK');
});

app.listen(3000);
```

### 5.4 Atoms Usage Snippets

**Basic Inline Embed**:

```tsx
import Cal from "@calcom/embed-react";

export default function BookingPage() {
  return (
    <div style={{ width: "100%", height: "100vh" }}>
      <Cal
        calLink="your-username/30min"
        style={{ width: "100%", height: "100%", overflow: "scroll" }}
      />
    </div>
  );
}
```

**Themed Embed with Custom Config**:

```tsx
import Cal, { getCalApi } from "@calcom/embed-react";
import { useEffect } from "react";

export default function CustomBooking() {
  useEffect(() => {
    (async function () {
      const cal = await getCalApi();
      cal("ui", {
        theme: "dark",
        styles: {
          branding: {
            brandColor: "#FF6B35",
            textColor: "#FFFFFF"
          }
        },
        hideEventTypeDetails: false
      });
    })();
  }, []);

  return (
    <Cal
      calLink="sales-team/demo"
      config={{
        layout: "week_view",
        theme: "dark"
      }}
    />
  );
}
```

### 5.5 Embedding Code Paths

**Vanilla JavaScript (Pop-up)**:

```html
<!DOCTYPE html>
<html>
<head>
  <title>Schedule a Meeting</title>
</head>
<body>
  <button id="cal-button">Book a Time</button>

  <script>
    (function (C, A, L) {
      let p = function (a, ar) { a.q.push(ar); };
      let d = C.document;
      C.Cal = C.Cal || function () {
        let cal = C.Cal;
        let ar = arguments;
        if (!cal.loaded) {
          cal.ns = {};
          cal.q = cal.q || [];
          d.head.appendChild(d.createElement("script")).src = A;
          cal.loaded = true;
        }
        if (ar[0] === L) {
          const api = function () { p(api, arguments); };
          const namespace = ar[1];
          api.q = api.q || [];
          typeof namespace === "string" 
            ? (cal.ns[namespace] = api) && p(api, ar) 
            : p(cal, ar);
          return;
        }
        p(cal, ar);
      };
    })(window, "https://app.cal.com/embed/embed.js", "init");

    Cal("init", { origin: "https://app.cal.com" });

    // Configure UI
    Cal("ui", {
      styles: { branding: { brandColor: "#0066FF" } },
      hideEventTypeDetails: false
    });

    // Add click handler
    document.getElementById('cal-button').addEventListener('click', function() {
      Cal("openModal", { calLink: "your-username/30min" });
    });
  </script>
</body>
</html>
```

### 5.6 Error Taxonomy & Actionable Messages

| HTTP Code | Error Type | Description | Action |
|-----------|-----------|-------------|--------|
| `400` | `INVALID_REQUEST` | Malformed request body or parameters | Check API docs for required fields |
| `401` | `UNAUTHORIZED` | Missing or invalid API key | Verify API key is correct and active |
| `403` | `FORBIDDEN` | Insufficient permissions | Check role/permissions for resource |
| `404` | `NOT_FOUND` | Resource doesn't exist | Verify UID/ID is correct |
| `409` | `CONFLICT` | Time slot already booked | Refresh availability and try another slot |
| `422` | `VALIDATION_ERROR` | Invalid data format | Review validation errors in response |
| `429` | `RATE_LIMIT_EXCEEDED` | Too many requests | Implement exponential backoff |
| `500` | `INTERNAL_ERROR` | Server error | Retry with exponential backoff; contact support if persists |

**Example Error Response**:

```json
{
  "status": "error",
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid booking request",
    "details": [
      {
        "field": "attendee.email",
        "message": "Invalid email format"
      },
      {
        "field": "start",
        "message": "Start time must be in the future"
      }
    ]
  }
}
```

---

## 6. Success Metrics

### 6.1 User Experience Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| **Time-to-First-Booking** | < 5 minutes from signup | Product analytics (signup → first booking confirmed) |
| **Booking Completion Rate** | > 80% | (Completed bookings / Started booking flows) × 100 |
| **Reschedule Rate** | < 10% | (Rescheduled bookings / Total bookings) × 100 |
| **No-Show Rate** | < 5% | (No-shows / Total bookings) × 100 |

### 6.2 Embedding & Integration Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| **Booking Conversion Lift (Embedded)** | +25% vs. redirect | A/B test: embedded widget vs. external redirect |
| **Embed Load Time** | < 1.5s p95 | Booker Atom initialization time |
| **Widget Error Rate** | < 0.5% | JavaScript errors per page load |

### 6.3 Developer & API Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| **Webhook Delivery Success Rate** | > 99% | (Successful deliveries / Total attempts) × 100 |
| **Webhook Retry Success** | > 95% after 3 retries | Successful delivery after retry / Failed first attempts |
| **API Response Time (p50)** | < 500ms | Server-side latency tracking |
| **API Response Time (p95)** | < 1s | Server-side latency tracking |
| **API Error Rate** | < 1% | (5xx responses / Total requests) × 100 |

### 6.4 Administrative & Enterprise Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| **Admin Setup Time** | < 30 minutes | Time from signup to first configured event type |
| **SSO Adoption (Enterprise)** | > 80% of users | (SSO logins / Total logins) × 100 |
| **Team Round-Robin Fairness** | ± 10% distribution | Std dev of bookings per team member |
| **Enterprise Deal Readiness** | 100% compliance docs | SOC 2, HIPAA, ISO 27001 available on request |

### 6.5 Availability & Performance

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| **Slot Search Performance** | < 1s p50, < 2s p95 | API monitoring |
| **Uptime (Enterprise)** | 99.95% | Uptime monitoring (Pingdom, Datadog) |
| **Incident Resolution Time** | < 4 hours (P1), < 24 hours (P2) | Ticketing system SLA tracking |

---

## 7. Risks & Open Questions

### 7.1 Calendar Provider Variance

**Risk**: Different calendar providers (Google, Outlook, Apple) have varying APIs, sync latencies, and conflict detection capabilities.

**Mitigation**:
- Implement unified calendar abstraction layer
- Cache availability with TTL to reduce provider API calls
- Provide fallback mechanisms for degraded provider service
- Document known provider limitations in API docs

**Open Questions**:
- How to handle provider outages gracefully?
- Should we implement local conflict detection as backup?
- What's acceptable sync delay for different use cases?

### 7.2 Race Conditions in Slot Holds

**Risk**: Multiple invitees attempting to book the same slot simultaneously could result in double-bookings.

**Mitigation**:
- Implement distributed locks (Redis) for slot reservations
- Optimistic locking with version checks on booking creation
- Brief (30-second) soft holds when slot is clicked
- Real-time slot availability updates via WebSocket

**Open Questions**:
- How long should soft holds last?
- Should we show "someone else is booking this slot" warnings?
- How to balance availability freshness vs. API load?

### 7.3 Webhook Retries

**Risk**: Failed webhook deliveries could result in missed notifications and data inconsistencies.

**Mitigation**:
- Exponential backoff retry strategy (immediate, 5s, 25s, 125s, 625s)
- Maximum 5 retry attempts over 15 minutes
- Dead letter queue for permanently failed webhooks
- Dashboard for monitoring failed deliveries
- Manual replay capability for critical events

**Open Questions**:
- Should we support guaranteed delivery for enterprise customers?
- How to handle subscriber endpoints with long downtime?
- Should webhook failures pause future deliveries?

### 7.4 Recurring/Series Edge Cases

**Risk**: Complex edge cases in recurring events (timezone changes, DST transitions, series modifications).

**Scenarios**:
- Invitee reschedules one instance of recurring series → Does whole series move?
- Host cancels middle occurrence → How are attendees notified?
- DST transition falls during scheduled recurring meeting → Which time is used?
- Series spans timezone change for traveling attendee → Handle timezone per occurrence?

**Mitigation**:
- Clear documentation of recurring event behavior
- Store each occurrence as separate booking with series linkage
- Explicit user confirmations for series-wide changes
- Timezone stored per occurrence, not per series

### 7.5 Seated Events & Attendee Privacy

**Risk**: In seated events (multiple attendees per slot), attendee privacy must be balanced with transparency.

**Considerations**:
- Should attendees see other attendees' names/emails?
- Different privacy levels for different event types (public webinar vs. private class)?
- GDPR implications of sharing attendee data

**Mitigation**:
- Configurable privacy settings per event type
- Anonymous attendee counts vs. full attendee lists
- Opt-in for attendee visibility
- Clear privacy disclosures in booking UI

### 7.6 Scaling Round-Robin Fairness

**Risk**: As teams grow large (50+ members), round-robin fairness becomes computationally expensive and harder to balance.

**Challenges**:
- Calculating "least recently booked" across large teams
- Handling concurrent bookings affecting assignment
- Members joining/leaving team mid-cycle
- Different availability patterns per team member

**Mitigation**:
- Cache assignment state with periodic recalculation
- Accept eventual consistency for very large teams
- Provide fairness metrics dashboard for admins to audit
- Support manual override for assignment when needed

---

## 8. Acceptance Criteria (Must-Pass Checks)

The following scenarios must be demonstrable in a working implementation:

### 8.1 API Booking Flow

**Test**: Create and retrieve a booking via API

**Steps**:
1. Generate API key from dashboard
2. Call `POST /v2/bookings` with valid event type, start time, and attendee
3. Verify response includes `uid` and status `accepted`
4. Call `GET /v2/bookings/{uid}` with returned UID
5. Verify retrieved booking matches created booking

**Pass Criteria**: Both API calls return 200 OK with matching booking data

### 8.2 Round-Robin Assignment

**Test**: Round-robin honors weights and least-recently-booked logic

**Setup**:
- Team with 3 members: Alice (weight: 1), Bob (weight: 2), Carol (weight: 1)
- Alice: last booked 1 hour ago
- Bob: last booked 2 days ago
- Carol: last booked 1 week ago

**Steps**:
1. Create 6 bookings via API or UI
2. Record which team member was assigned each booking

**Expected Assignment Order**:
1. Carol (least recent, weight 1)
2. Bob (next least recent, weight 2)
3. Bob (weight 2 gets 2nd booking)
4. Alice (least recent among remaining)
5. Carol (back to least recent)
6. Bob (weight 2 gets another)

**Pass Criteria**: Assignments follow expected order with ±1 tolerance for race conditions

### 8.3 Routing Form Conditional Logic

**Test**: Routing form directs to different event types based on responses

**Setup**:
- Routing form with question: "What service do you need?"
  - Option A: "Sales Demo" → Routes to Event Type A
  - Option B: "Technical Support" → Routes to Event Type B

**Steps**:
1. Fill form and select Option A
2. Verify redirected to Event Type A booking page
3. Fill form and select Option B
4. Verify redirected to Event Type B booking page

**Pass Criteria**: Correct event type shown based on form response

### 8.4 Webhook Delivery & Verification

**Test**: Webhooks fire for lifecycle events with verified signatures

**Setup**:
- Configure webhook subscription for `BOOKING_CREATED`, `BOOKING_RESCHEDULED`, `BOOKING_CANCELLED`
- Deploy webhook endpoint with signature verification

**Steps**:
1. Create a booking → Webhook fires with `BOOKING_CREATED`
2. Reschedule the booking → Webhook fires with `BOOKING_RESCHEDULED`
3. Cancel the booking → Webhook fires with `BOOKING_CANCELLED`
4. For each webhook, verify `X-Cal-Signature-256` header matches HMAC of body

**Pass Criteria**: All 3 webhooks delivered successfully, signatures verified

### 8.5 Booker Atom Rendering

**Test**: Booker Atom displays available slots and completes booking

**Setup**:
- React app with Booker Atom component
- Valid event type configured with availability

**Steps**:
1. Load page with `<Cal calLink="username/eventtype" />`
2. Verify available time slots render
3. Click a time slot
4. Fill attendee information
5. Submit booking
6. Verify confirmation screen shows

**Pass Criteria**: Full booking flow completes without errors

### 8.6 SSO Login

**Test**: User can log in via SAML or OIDC

**Setup**:
- Configure SAML or OIDC integration with test IdP

**Steps**:
1. Navigate to login page
2. Click "Log in with SSO"
3. Redirected to IdP
4. Authenticate with IdP credentials
5. Redirected back to Cal.com
6. Verify logged in as correct user

**Pass Criteria**: SSO login succeeds, user session established

### 8.7 Embedding (Inline & Pop-up)

**Test**: Embed works in both inline and pop-up modes

**Setup**:
- HTML page with inline `<iframe>` or Cal component
- HTML page with pop-up button trigger

**Steps**:
1. **Inline**: Load page, verify booking widget displays inline
2. **Pop-up**: Click button, verify modal overlay opens with booking widget
3. Complete booking in both modes
4. Verify confirmations received

**Pass Criteria**: Both embed modes render correctly and support full booking flow

---

## Appendix A — Key API & UI References

This appendix provides a summary of key resources for implementing or integrating with Cal.com. For detailed information, refer to the official Cal.com documentation at https://cal.com/docs.

### A.1 API v2 Documentation

**Base URL**: `https://api.cal.com/v2`

**Authentication**: 
```
Authorization: Bearer cal_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**Core Resources**:
- **Bookings**: `/v2/bookings` (POST, GET, PATCH, DELETE)
- **Event Types**: `/v2/event-types` (GET, POST, PATCH, DELETE)
- **Availability**: `/v2/slots/available` (GET)
- **Webhooks**: `/v2/webhooks` (POST, GET, PATCH, DELETE)
- **Users**: `/v2/users/me` (GET)
- **Teams**: `/v2/teams` (GET, POST)

**Key Endpoints Summary**:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v2/bookings` | POST | Create a new booking |
| `/v2/bookings/{uid}` | GET | Retrieve booking by UID |
| `/v2/bookings/{uid}` | PATCH | Reschedule booking |
| `/v2/bookings/{uid}` | DELETE | Cancel booking |
| `/v2/slots/available` | GET | Get available time slots |
| `/v2/webhooks` | POST | Create webhook subscription |
| `/v2/event-types` | GET | List event types |

### A.2 Webhooks Documentation

**Webhook Creation**: `POST /v2/webhooks`

**Available Triggers**:
- `BOOKING_CREATED`
- `BOOKING_RESCHEDULED`
- `BOOKING_CANCELLED`
- `BOOKING_PAID`
- `BOOKING_NO_SHOW`
- `MEETING_STARTED`
- `MEETING_ENDED`
- `RECORDING_READY`
- `FORM_SUBMITTED`
- `OOO_CREATED`

**Payload Structure**:
```json
{
  "triggerEvent": "BOOKING_CREATED",
  "createdAt": "2025-11-15T14:05:23Z",
  "payload": {
    "uid": "bk_abc123def456",
    "eventTypeId": 123456,
    "title": "Meeting Title",
    "startTime": "2025-11-15T14:00:00Z",
    "endTime": "2025-11-15T14:30:00Z",
    "organizer": {...},
    "attendees": [...],
    "location": "...",
    "metadata": {...}
  }
}
```

**Signature Verification**: 
- Header: `X-Cal-Signature-256`
- Algorithm: HMAC-SHA256 of request body
- Secret: Configured during webhook creation

### A.3 Availability Endpoints

**V2 (Recommended)**: 
```
GET /v2/slots/available?eventTypeId={id}&startTime={ISO}&endTime={ISO}&timeZone={tz}
```

**V1 (Legacy)**:
```
GET /v1/availability?userId={id}&dateFrom={date}&dateTo={date}&eventTypeId={id}
```

**Response Format** (V2):
```json
{
  "status": "success",
  "data": {
    "slots": {
      "2025-11-20": ["09:00:00", "10:00:00", "11:00:00"],
      "2025-11-21": ["14:00:00", "15:00:00"]
    }
  }
}
```

### A.4 Atoms (Booker Component)

**Installation**:
```bash
npm install @calcom/embed-react
```

**Basic Usage**:
```tsx
import Cal from "@calcom/embed-react";

<Cal calLink="your-username/event-type" />
```

**Advanced Configuration**:
```tsx
import Cal, { getCalApi } from "@calcom/embed-react";

useEffect(() => {
  (async function () {
    const cal = await getCalApi();
    cal("ui", {
      theme: "light",
      styles: { branding: { brandColor: "#0066FF" } }
    });
  })();
}, []);

<Cal 
  calLink="username/event" 
  config={{ layout: "month_view" }}
/>
```

### A.5 Conferencing Integrations

**Supported Providers**:
- **Zoom**: OAuth integration, auto-generates meeting links
- **Google Meet**: Via Google Calendar OAuth
- **Microsoft Teams**: Via Microsoft 365 OAuth
- **Whereby**: API integration
- **Daily.co**: Embedded video
- **Custom**: Arbitrary meeting URLs

**Configuration**: Navigate to Settings → Apps → Video Conferencing

### A.6 Calendar Providers

**Supported Calendars**:
- Google Calendar (OAuth 2.0)
- Microsoft 365 / Outlook (OAuth 2.0)
- Apple Calendar (CalDAV)
- iCloud Calendar (CalDAV)

**Unified Calendar Concept**:
- Single abstraction layer for all providers
- Automatic conflict detection across multiple calendars
- Bidirectional sync (Cal.com ↔ Calendar Provider)
- Provider-agnostic API

**Configuration**: Settings → Calendars → Connect Calendar

### A.7 Enterprise & Compliance

**Compliance Resources**:
- **SOC 2 Type II**: Request report via enterprise support
- **HIPAA BAA**: Contact sales for Business Associate Agreement
- **ISO 27001**: Certificate available upon request
- **GDPR**: Data Processing Agreement in enterprise contracts

**Security Features**:
- Encryption at rest (AES-256)
- Encryption in transit (TLS 1.2+)
- Regular penetration testing
- Vulnerability disclosure program
- Security audit logs

**Deployment Options**:
- Cloud Multi-tenant: `https://app.cal.com`
- Dedicated Cloud: `https://{customer}.cal.com`
- On-Premises: Self-hosted via Docker or Kubernetes

### A.8 SSO Setup

**SAML 2.0 Configuration**:
1. Navigate to Settings → Security → SSO
2. Select "SAML 2.0"
3. Upload IdP metadata XML or enter manually
4. Configure attribute mappings (email, name, groups)
5. Test with sample user
6. Enable for organization

**OIDC Configuration**:
1. Navigate to Settings → Security → SSO
2. Select "OpenID Connect"
3. Enter Client ID and Client Secret
4. Configure authorization and token URLs
5. Set redirect URI: `https://app.cal.com/api/auth/callback/oidc`
6. Test connection
7. Enable for organization

**Supported IdPs**:
- Okta
- Azure Active Directory
- Google Workspace
- Auth0
- OneLogin
- Custom SAML 2.0 / OIDC providers

### A.9 Rate Limiting & Best Practices

**Best Practices**:
1. **Implement Exponential Backoff**: When receiving 429 errors
2. **Use Webhooks**: Instead of polling for booking updates
3. **Cache Availability**: Reduce load on slot search endpoints
4. **Idempotency Keys**: For mutation operations
5. **Paginate Results**: Use cursor-based pagination for large datasets
6. **Monitor Rate Limits**: Check `X-RateLimit-*` headers
7. **Secure Secrets**: Never expose `cal_live_` or `whsec_` keys in client code

**Error Handling Pattern**:
```javascript
async function createBookingWithRetry(bookingData, maxRetries = 3) {
  let retries = 0;
  let delay = 1000; // Start with 1 second
  
  while (retries < maxRetries) {
    try {
      const response = await fetch('https://api.cal.com/v2/bookings', {
        method: 'POST',
        headers: {
          'Authorization': 'Bearer cal_live_xxx',
          'Content-Type': 'application/json',
          'Idempotency-Key': generateIdempotencyKey(bookingData)
        },
        body: JSON.stringify(bookingData)
      });
      
      if (response.ok) {
        return await response.json();
      }
      
      if (response.status === 429) {
        // Rate limited - wait and retry
        await new Promise(resolve => setTimeout(resolve, delay));
        delay *= 2; // Exponential backoff
        retries++;
        continue;
      }
      
      // Other errors - don't retry
      throw new Error(`API error: ${response.status}`);
      
    } catch (error) {
      if (retries === maxRetries - 1) throw error;
      retries++;
    }
  }
}
```

### A.10 Testing & Sandbox Environment

**Development Mode**:
- Use test API keys (prefix: `cal_test_`)
- Test webhooks with tools like ngrok or webhook.site
- Sandbox event types for testing without affecting production

**Recommended Testing Tools**:
- **Postman**: API testing and collection sharing
- **ngrok**: Expose local webhook endpoints
- **webhook.site**: Inspect webhook payloads
- **Insomnia**: REST client alternative to Postman

---

## Document Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-11-06 | Product Team | Initial release of Cal.com Parity & Capability Analysis PRD |

---

## Acknowledgments

This PRD is based on publicly available documentation, API specifications, and feature analysis of Cal.com (https://cal.com). It is intended for teams evaluating Cal.com for integration or building compatible scheduling infrastructure.

**Security Reminder**: Always treat API keys and webhook secrets as sensitive credentials. Use environment variables, never commit secrets to version control, and rotate keys regularly.

---

**End of Document**
