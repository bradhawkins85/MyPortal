# SMTP2Go Integration

MyPortal supports enhanced email delivery and tracking through SMTP2Go, a professional email delivery service. SMTP2Go provides comprehensive delivery tracking, open and click analytics, and better deliverability compared to traditional SMTP relay.

## Features

- **Reliable Delivery**: Professional email delivery with high deliverability rates
- **Delivery Tracking**: Track when emails are delivered to recipients
- **Open Tracking**: Know when recipients open your emails
- **Click Tracking**: Monitor link clicks in your emails
- **Bounce Handling**: Automatic bounce detection and tracking
- **Spam Reports**: Track spam complaints
- **Webhook Events**: Real-time delivery status updates
- **Fallback Support**: Automatic fallback to SMTP relay if API fails

## Prerequisites

1. **SMTP2Go Account**: Sign up at [https://www.smtp2go.com/](https://www.smtp2go.com/)
2. **API Key**: Generate an API key from your SMTP2Go dashboard
3. **Webhook Configuration**: Set up webhooks for delivery tracking (optional but recommended)

## Configuration

### Step 1: Get Your API Key

1. Log in to your SMTP2Go account
2. Navigate to **Settings** → **API Keys**
3. Click **Create New API Key**
4. Give it a descriptive name (e.g., "MyPortal Integration")
5. Copy the generated API key

### Step 2: Configure Environment Variables

Add the following to your `.env` file:

```bash
# SMTP2Go API Integration
SMTP2GO_API_KEY=your_api_key_here
SMTP2GO_WEBHOOK_SECRET=your_webhook_secret_here
```

To generate a secure webhook secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Step 3: Enable the SMTP2Go Module

1. Log in to MyPortal as a super admin
2. Navigate to **Admin** → **Integration Modules**
3. Find the **SMTP2Go** module
4. Click **Configure**
5. Enter your API key
6. Enable tracking options:
   - **Enable Tracking**: Master switch for tracking
   - **Track Opens**: Track email opens
   - **Track Clicks**: Track link clicks
7. Enter your webhook secret
8. Toggle the module to **Enabled**
9. Click **Save**

### Step 4: Configure Webhooks in SMTP2Go

To receive delivery status updates, set up webhooks in SMTP2Go:

1. Log in to your SMTP2Go account
2. Navigate to **Settings** → **Webhooks**
3. Click **Create New Webhook**
4. Configure the webhook:
   - **URL**: `https://your-portal-domain.com/api/webhooks/smtp2go/events`
   - **Events**: Select the events you want to track:
     - `delivered` - Email successfully delivered
     - `opened` - Email was opened by recipient
     - `clicked` - Link in email was clicked
     - `bounced` - Email bounced (hard or soft)
     - `spam` - Email marked as spam
   - **Secret**: Enter the webhook secret you generated
5. Click **Create Webhook**

## Usage

Once configured, SMTP2Go will be used automatically for all outgoing emails from MyPortal. The system will:

1. Send emails via SMTP2Go API instead of SMTP relay
2. Automatically generate unique tracking IDs for each email
3. Store SMTP2Go message IDs for correlation
4. Process webhook events as they arrive
5. Update ticket reply records with delivery status
6. Fall back to SMTP relay if SMTP2Go API is unavailable

## Tracking Data

Email tracking data is stored in the database and associated with ticket replies. You can view tracking information for each email:

- **Sent At**: When the email was sent
- **Delivered At**: When the email was delivered to the recipient's server
- **Opened At**: When the recipient first opened the email
- **Open Count**: Number of times the email was opened
- **Bounced At**: When the email bounced (if applicable)
- **Click Events**: List of links clicked with timestamps

## API Reference

### SMTP2Go Service (`app/services/smtp2go.py`)

#### `send_email_via_api()`

Send an email using SMTP2Go API.

```python
from app.services import smtp2go

result = await smtp2go.send_email_via_api(
    to=["recipient@example.com"],
    subject="Email Subject",
    html_body="<p>Email content</p>",
    text_body="Email content",  # Optional
    sender="sender@example.com",  # Optional
    reply_to="reply@example.com",  # Optional
    tracking_id="unique-tracking-id",  # Optional
)
```

#### `process_webhook_event()`

Process a webhook event from SMTP2Go.

```python
result = await smtp2go.process_webhook_event(
    event_type="delivered",
    event_data={
        "email_id": "smtp2go-message-id",
        "recipient": "recipient@example.com",
        "timestamp": "2025-01-01T12:00:00Z",
    }
)
```

#### `get_email_stats()`

Get email statistics for a ticket reply.

```python
stats = await smtp2go.get_email_stats(reply_id=123)
```

### Webhook Endpoint

**POST** `/api/webhooks/smtp2go/events`

Receives webhook events from SMTP2Go.

Headers:
- `X-Smtp2go-Signature`: HMAC-SHA256 signature for webhook verification

Body: Single event object from SMTP2Go (JSON object)

## Security

### Webhook Verification

All webhook requests are verified using HMAC-SHA256 signatures. The webhook secret must be configured in both MyPortal and SMTP2Go for verification to work.

MyPortal supports multiple signature formats:

1. **Timestamp-based format** (SMTP2Go's current format):
   - Header format: `t=<timestamp>,v1=<signature>`
   - Signature computed as: `HMAC-SHA256(secret, "<timestamp>.<payload>")`
   - This is the primary format used by SMTP2Go

2. **Legacy formats** (for backwards compatibility):
   - Plain hex digest: `HMAC-SHA256(secret, payload)`
   - Base64 encoded: Base64 of HMAC-SHA256 digest
   - sha256= prefixed: `sha256=<hex_digest>`

If webhook verification fails:
- A warning is logged
- The request is rejected with a 401 Unauthorized status
- Events are not processed

### API Key Security

- API keys are stored in the integration module settings
- Never commit API keys to version control
- Use environment variables for configuration
- Rotate API keys periodically

## Troubleshooting

### Emails Not Sending via SMTP2Go

1. Check that the SMTP2Go module is enabled in Integration Modules
2. Verify your API key is correct
3. Check application logs for error messages
4. Ensure `PORTAL_URL` is configured correctly
5. Test API connectivity: `curl -X POST https://api.smtp2go.com/v3/email/send`

### Webhooks Not Working

1. Verify webhook URL is accessible from the internet
2. Check that webhook secret matches in both systems
3. Review SMTP2Go webhook logs for delivery failures
4. Check application logs for webhook processing errors
5. Ensure firewall rules allow incoming webhook requests

### Tracking Not Working

1. Verify tracking is enabled in SMTP2Go module settings
2. Check that `PORTAL_URL` is configured
3. Ensure webhooks are properly configured in SMTP2Go
4. Review email_tracking_events table for recorded events

### Tracking Data Not Being Saved

If emails are sent successfully but tracking data is not persisted in the database:

1. **Check Migration Status**: Ensure migration `143_smtp2go_tracking.sql` has been applied
   - Verify these columns exist in `ticket_replies` table:
     - `email_tracking_id`
     - `smtp2go_message_id`
     - `email_sent_at`
     - `email_delivered_at`
     - `email_bounced_at`
2. **Review Application Logs**: Search for errors with "Failed to record SMTP2Go email metadata"
   - With improved logging (Nov 2025+), logs now include full exception details
   - Look for database errors like "Unknown column" or "field list" errors
3. **Verify Database Permissions**: Ensure the application has UPDATE permissions on `ticket_replies`
4. **Check for Silent Failures**: 
   - If using older versions, database errors may be logged but not surfaced
   - Upgrade to latest version for improved error visibility
5. **Note**: As of the latest update, tracking is persisted even if SMTP2Go doesn't return a message ID in the API response

### Fallback to SMTP Relay

If SMTP2Go API fails, the system automatically falls back to SMTP relay:

1. Check logs for SMTP2Go error messages
2. Verify SMTP relay is properly configured
3. Monitor for API issues in SMTP2Go dashboard

## Migration from Plausible Analytics

If you're migrating from Plausible Analytics email tracking:

1. Configure SMTP2Go as described above
2. Enable the SMTP2Go module
3. Disable the Plausible Analytics module (optional)
4. SMTP2Go will take over email tracking automatically
5. Existing Plausible tracking continues to work as a fallback

### Key Differences

| Feature | Plausible Analytics | SMTP2Go |
|---------|-------------------|---------|
| Delivery Tracking | ❌ | ✅ |
| Open Tracking | ✅ | ✅ |
| Click Tracking | ✅ | ✅ |
| Bounce Detection | ❌ | ✅ |
| Spam Reports | ❌ | ✅ |
| Real-time Webhooks | ❌ | ✅ |
| API-based Sending | ❌ | ✅ |

## Best Practices

1. **Enable All Tracking**: Enable delivery, open, and click tracking for maximum visibility
2. **Configure Webhooks**: Set up webhooks for real-time status updates
3. **Monitor Bounces**: Review bounce reports and remove invalid addresses
4. **Test Regularly**: Send test emails to verify tracking is working
5. **Review Statistics**: Use tracking data to improve email communication
6. **Rotate Secrets**: Periodically rotate API keys and webhook secrets
7. **Monitor Logs**: Watch application logs for SMTP2Go errors

## Limits and Quotas

SMTP2Go accounts have different limits based on your plan:

- **Free Plan**: 1,000 emails/month
- **Paid Plans**: Higher limits based on subscription

Monitor your usage in the SMTP2Go dashboard to avoid hitting limits.

## Email Templates

MyPortal includes pre-defined email templates for common use cases. Templates provide standardized formatting and ensure consistent email structure.

### Available Templates

1. **password_reset** - Password reset emails with secure reset links
2. **invoice** - Invoice notification emails
3. **alert** - System alerts and notifications
4. **notification** - General purpose notifications
5. **ticket_reply** - Support ticket reply notifications
6. **welcome** - New user welcome emails

### Using Templates in Trigger Actions

When configuring trigger actions for the SMTP2Go module, you can use templates instead of manually crafting payloads:

```json
{
  "template": "password_reset",
  "recipients": ["user@example.com"],
  "variables": {
    "recipient_name": "John Doe",
    "reset_link": "https://portal.example.com/reset/abc123",
    "expiry_time": "1 hour"
  },
  "sender": "noreply@example.com"
}
```

### Template Variables

Each template has recommended variables:

#### password_reset
- `recipient_name` - Name of the user
- `reset_link` - URL to reset password
- `expiry_time` - How long the link is valid

#### invoice
- `invoice_number` - Invoice number
- `company_name` - Company name
- `amount` - Invoice amount
- `due_date` - Payment due date
- `invoice_link` - URL to view invoice

#### alert
- `alert_type` - Type of alert
- `alert_message` - Alert details
- `severity` - Alert severity level
- `timestamp` - When alert occurred
- `action_link` - URL to take action

#### notification
- `title` - Notification title
- `message` - Notification message
- `action_text` - Call-to-action text
- `action_link` - URL for action

#### ticket_reply
- `ticket_id` - Ticket number
- `ticket_subject` - Ticket subject
- `reply_content` - Reply content (HTML)
- `reply_author` - Who replied
- `ticket_link` - URL to view ticket

#### welcome
- `recipient_name` - Name of new user
- `company_name` - Company name
- `login_link` - URL to login
- `support_email` - Support contact email

### Programmatic Template Usage

You can also use templates programmatically in Python:

```python
from app.services import smtp2go

# List available templates
templates = smtp2go.list_email_templates()

# Get a specific template
template = smtp2go.get_email_template("password_reset")

# Format a template with variables
payload = smtp2go.format_template_payload(
    "password_reset",
    {
        "recipient_name": "John Doe",
        "reset_link": "https://example.com/reset/abc123",
        "expiry_time": "1 hour",
    },
    ["user@example.com"],
    "noreply@example.com",
)

# Send the email
result = await smtp2go.send_email_via_api(**payload)
```

### Legacy Direct Payload Format

The original direct payload format is still supported for backward compatibility:

```json
{
  "recipients": ["user@example.com"],
  "subject": "Email Subject",
  "html": "<p>HTML body</p>",
  "text": "Plain text body",
  "sender": "noreply@example.com"
}
```

## Common Issues and Solutions

### 400 Bad Request Error

**Cause**: Missing required fields in API request

**Solutions**:
1. Ensure `sender` field is provided or `SMTP_USER` is configured in settings
2. Verify all required fields are present: `to`, `subject`, `html_body` or `text_body`
3. Check logs for detailed error information
4. Validate email addresses are properly formatted

Example error message:
```
API request failed with 400 Bad Request. 
Response: {"error": "Invalid sender"}. 
Check that all required fields are present and valid.
```

### Missing Sender Field

**Cause**: The `sender` field is required by SMTP2Go but not provided

**Solution**: Provide sender in one of these ways:
1. Pass `sender` parameter to `send_email_via_api()`
2. Configure `SMTP_USER` in environment variables
3. Include `sender` in trigger action payload

```python
# Option 1: Explicit sender
await smtp2go.send_email_via_api(
    to=["user@example.com"],
    subject="Test",
    html_body="<p>Test</p>",
    sender="noreply@example.com"  # ✅ Explicit sender
)

# Option 2: Use SMTP_USER from settings
# Set SMTP_USER=noreply@example.com in .env
await smtp2go.send_email_via_api(
    to=["user@example.com"],
    subject="Test",
    html_body="<p>Test</p>"
    # ✅ sender defaults to SMTP_USER
)
```


## Support

For SMTP2Go-specific issues:
- Documentation: [https://apidocs.smtp2go.com/](https://apidocs.smtp2go.com/)
- Support: [https://www.smtp2go.com/support/](https://www.smtp2go.com/support/)

For MyPortal integration issues:
- Check application logs
- Review this documentation
- Contact your system administrator
