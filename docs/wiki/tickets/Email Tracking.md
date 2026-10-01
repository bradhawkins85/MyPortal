# Email Tracking

MyPortal includes built-in email tracking for outgoing mail. Tracking events are stored locally in MyPortal and can be enriched by SMTP2Go webhook data when that module is enabled.

## Features

- **Email Open Tracking**: Track when recipients open emails using invisible tracking pixels
- **Link Click Tracking**: Monitor which links recipients click in emails
- **Privacy-First**: Events are stored locally in MyPortal
- **Visual Indicators**: Conversation history shows read/unread status for sent emails
- **Detailed Analytics**: View open counts, timestamps, and click-through data
- **Per-Recipient Delivery Status**: Multi-recipient replies can show recipient-level delivery and open data when SMTP2Go webhooks are configured

## How It Works

### Email Opens
When an email is sent with tracking enabled, a 1x1 transparent tracking pixel (GIF image) is inserted at the end of the email HTML. When the recipient's email client loads this image, MyPortal records an "open" event.

### Link Clicks
All HTTP/HTTPS links in tracked emails are automatically rewritten to route through MyPortal's tracking endpoint. When a recipient clicks a link, the click is recorded before redirecting to the original destination URL.

### Data Storage
- **Local Database**: All email tracking events are stored in the `email_tracking_events` table
- **Ticket Replies**: Summary data (first open time, open count) is stored in the `ticket_replies` table
- **SMTP2Go Enhancements**: When enabled, SMTP2Go webhook events can add delivery, bounce, spam, and per-recipient state

## Usage

1. Send emails through MyPortal with tracking enabled
2. Tracking links and pixels are added automatically
3. View tracking status in ticket conversations:
   - Opened emails show an "Opened" badge
   - Unopened emails show "Sent" or "Pending" status

## Troubleshooting

- Ensure `PORTAL_URL` is configured correctly
- Verify tracking events are being recorded in the database
- Check that recipients can reach the portal URL used in tracking links and pixels
- If you use SMTP2Go, verify webhook delivery and signature configuration
