# Email Tracking with Plausible Analytics

MyPortal includes built-in email tracking functionality powered by Plausible Analytics integration. This privacy-focused feature allows you to track email opens and link clicks for all outgoing emails sent through the system. Additionally, MyPortal can integrate with Plausible for app-wide page view analytics.

## Features

### Email Tracking
- **Email Open Tracking**: Track when recipients open emails using invisible tracking pixels
- **Link Click Tracking**: Monitor which links recipients click in emails
- **Privacy-First**: Events are stored locally and optionally synced to Plausible
- **Visual Indicators**: Conversation history shows read/unread status for sent emails
- **Detailed Analytics**: View open counts, timestamps, and click-through data

### App Analytics (Page Views)
- **Automatic Page Tracking**: Track page views across the entire application
- **User Journey Analysis**: Understand how users navigate through MyPortal
- **Privacy-Focused**: Uses Plausible's cookieless tracking
- **Self-Hosted Support**: Works with both cloud and self-hosted Plausible instances

## Self-Hosted Plausible Support

MyPortal fully supports **self-hosted Plausible installations**. Simply configure the `PLAUSIBLE_BASE_URL` environment variable to point to your self-hosted instance:

```bash
# For self-hosted Plausible
PLAUSIBLE_BASE_URL=https://analytics.yourcompany.com

# For cloud Plausible (default)
PLAUSIBLE_BASE_URL=https://plausible.io
```

The integration works identically with both cloud and self-hosted Plausible instances.

## How It Works

### Email Opens
When an email is sent with tracking enabled, a 1x1 transparent tracking pixel (GIF image) is inserted at the end of the email HTML. When the recipient's email client loads this image, MyPortal records an "open" event.

### Link Clicks
All HTTP/HTTPS links in tracked emails are automatically rewritten to route through MyPortal's tracking endpoint. When a recipient clicks a link, the click is recorded before redirecting to the original destination URL.

### App Page Views
When the Plausible module is enabled, MyPortal automatically includes the Plausible tracking script in all pages. This tracks:
- Page views and unique visitors
- Navigation patterns and user journeys
- Referrer sources and entry/exit pages
- Device types and browsers

### Data Storage
- **Local Database**: All email tracking events are stored in the `email_tracking_events` table
- **Ticket Replies**: Summary data (first open time, open count) is stored in the `ticket_replies` table
- **Plausible Sync** (Optional): Events can be forwarded to a Plausible Analytics instance for dashboard analytics

## Configuration

### Environment Variables

Add these variables to your `.env` file:

```bash
# Plausible Analytics Base URL (supports self-hosted instances)
PLAUSIBLE_BASE_URL=https://plausible.io

# Your site domain registered in Plausible
PLAUSIBLE_SITE_DOMAIN=myportal.example.com

# API key for Plausible (optional)
PLAUSIBLE_API_KEY=

# Enable tracking of email opens (default: true)
PLAUSIBLE_TRACK_OPENS=true

# Enable tracking of link clicks (default: true)
PLAUSIBLE_TRACK_CLICKS=true

# Send events to Plausible Analytics (default: false)
PLAUSIBLE_SEND_TO_PLAUSIBLE=false
```

### Integration Module

The Plausible Analytics module is automatically created during database migration. To configure it:

1. Navigate to **Admin → Integration Modules**
2. Find the **Plausible Analytics** module (📊 icon)
3. Click **Configure** to edit settings:
   - **Base URL**: Your Plausible instance URL (cloud or self-hosted)
   - **Site Domain**: Your portal's domain name
   - **API Key**: (Optional) Plausible API key for event forwarding
   - **Track Opens**: Enable/disable email open tracking
   - **Track Clicks**: Enable/disable link click tracking
   - **Send to Plausible**: Enable forwarding events to Plausible

### Enabling App Analytics

Once the Plausible module is configured and enabled:

1. **Email Tracking**: Works automatically for emails sent with `enable_tracking=True`
2. **App Analytics (Page Views)**: Automatically enabled across all pages
   - The Plausible tracking script is automatically injected into every page
   - Tracks page views, user journeys, and navigation patterns
   - No additional configuration needed
   - Privacy-friendly: no cookies, GDPR/CCPA compliant

To view analytics:
- Log into your Plausible dashboard (cloud or self-hosted)
- Select your site domain to see page views, sources, and user behavior
- Email tracking events appear as custom events (`email_open`, `email_click`)

## Usage

### Setting Up Plausible

#### Option 1: Cloud Plausible (plausible.io)

1. Sign up at https://plausible.io
2. Add your site domain (e.g., `myportal.example.com`)
3. Configure MyPortal with:
   ```bash
   PLAUSIBLE_BASE_URL=https://plausible.io
   PLAUSIBLE_SITE_DOMAIN=myportal.example.com
   ```
4. Enable the module in Admin → Integration Modules

#### Option 2: Self-Hosted Plausible

1. Deploy Plausible using their self-hosting guide: https://plausible.io/docs/self-hosting
2. Add your site domain in your Plausible admin panel
3. Configure MyPortal with:
   ```bash
   PLAUSIBLE_BASE_URL=https://analytics.yourcompany.com
   PLAUSIBLE_SITE_DOMAIN=myportal.example.com
   ```
4. Enable the module in Admin → Integration Modules

The integration works identically with both options.

### Enabling Email Tracking

To enable tracking when sending an email programmatically:

```python
from app.services import email as email_service

# Send email with tracking enabled
sent, event_metadata = await email_service.send_email(
    subject="Ticket Update",
    recipients=["user@example.com"],
    html_body="<p>Your ticket has been updated.</p>",
    enable_tracking=True,
    ticket_reply_id=123,  # Required for tracking
)
```

### Viewing Tracking Status

In the ticket detail page, the conversation history shows status badges for tracked emails:

- **✓ Read** (green badge): Email has been opened
- **📧 Sent** (gray badge): Email sent but not yet opened
- Hover over badges to see open count and details

### Viewing Analytics

**Page Analytics:**
- Log into your Plausible dashboard
- View page views, unique visitors, bounce rates
- See top pages, referrer sources, and user locations
- Analyze user navigation patterns

**Email Analytics:**
- Custom events appear as `email_open` and `email_click`
- Filter by event properties to analyze email engagement
- Track click-through rates and email effectiveness

### API Endpoints

The following endpoints are available for tracking:

- `GET /api/email-tracking/pixel/{tracking_id}.gif` - Serves tracking pixel (internal use)
- `GET /api/email-tracking/click?tid={tracking_id}&url={destination}` - Click redirect (internal use)
- `GET /api/email-tracking/status/{tracking_id}` - Get tracking status for an email

## Database Schema

### `ticket_replies` Table (New Columns)

```sql
email_tracking_id VARCHAR(64)     -- Unique tracking ID
email_sent_at DATETIME(6)         -- When email was sent
email_opened_at DATETIME(6)       -- First open timestamp
email_open_count INT              -- Number of opens
```

### `email_tracking_events` Table

```sql
id INT AUTO_INCREMENT PRIMARY KEY
tracking_id VARCHAR(64)           -- Links to ticket_replies.email_tracking_id
event_type ENUM('open', 'click')  -- Type of event
event_url VARCHAR(2048)           -- Clicked URL (for clicks)
user_agent TEXT                   -- Browser/client info
ip_address VARCHAR(45)            -- Client IP
referrer VARCHAR(2048)            -- HTTP referrer
occurred_at DATETIME(6)           -- Event timestamp
plausible_sent TINYINT(1)         -- Sent to Plausible flag
plausible_sent_at DATETIME(6)     -- When sent to Plausible
```

## Privacy Considerations

- **Local Storage**: By default, all events are stored locally in MyPortal's database
- **No Cookies**: Tracking does not use cookies and is GDPR/CCPA compliant
- **Optional External Sync**: Forwarding to Plausible is disabled by default
- **IP Anonymization**: Consider implementing IP anonymization if forwarding to Plausible
- **User Control**: Tracking can be disabled per module configuration

## Security

- Tracking IDs are generated using cryptographically secure random tokens
- No sensitive data is included in tracking payloads
- Tracking endpoints do not require authentication (by design)
- All tracking URLs use the configured `PORTAL_URL` from environment

## Troubleshooting

### Tracking Not Working

1. Check that `PORTAL_URL` is correctly configured in `.env`
2. Verify Plausible module is enabled in Admin → Integration Modules
3. Ensure `enable_tracking=True` and `ticket_reply_id` is provided when sending emails
4. Check that SMTP is properly configured

### App Analytics Not Showing

1. Verify Plausible module is enabled in Admin → Integration Modules
2. Check that `base_url` and `site_domain` are configured correctly
3. Make sure your site domain is added to your Plausible dashboard
4. Check browser console for any script loading errors

### Email Opens Not Detected

- Some email clients block external images by default
- Corporate firewalls may block tracking pixels
- Plain text emails do not support tracking pixels

### Click Tracking Not Working

- Verify `PORTAL_URL` is accessible from the internet
- Check that links are HTTP/HTTPS (not mailto: or other protocols)
- Ensure tracking is enabled in module configuration

## Performance

- Tracking adds minimal overhead (<100ms per email)
- Tracking pixel is a tiny 43-byte GIF image
- Click redirects are fast (<50ms) with immediate redirect
- Database queries use indexes for optimal performance
- Plausible script is lightweight (< 1KB) and loaded asynchronously

## Future Enhancements

Potential improvements for future versions:

- Heatmap visualization of click patterns
- Email client detection from user agent
- Geographic tracking of opens/clicks
- A/B testing support for email content
- Automated follow-up based on engagement
- Export tracking data to CSV/JSON
- Real-time dashboard with charts
- Advanced funnel analysis

## Related Documentation

- [Plausible Analytics Documentation](https://plausible.io/docs)
- [Plausible Events API](https://plausible.io/docs/events-api)
- [Plausible Self-Hosting Guide](https://plausible.io/docs/self-hosting)
- [Integration Modules Guide](../docs/integration-modules.md)
- [Email Service Documentation](../docs/email-service.md)
