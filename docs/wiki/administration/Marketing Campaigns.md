# Marketing Campaigns

Email campaigns send one-to-one emails to customer contacts, for example to
tell every billing contact at companies running Bitdefender about an upcoming
invoicing or product change. They live under *Marketing → Email campaigns*
(`/admin/marketing/campaigns`) and need the `marketing.access` permission.

## Creating a campaign

- **Type** – *Updates* for service, product and account notices, or *Sales*.
  Sales emails get an unsubscribe footer and a `List-Unsubscribe` header, and
  contacts who unsubscribed are skipped. Unsubscribing only stops Sales emails;
  Updates still arrive.
- **Subject and body** – pick a [message template](Message%20Templates.md) or
  write HTML on the campaign. Both the subject and body accept variables:

  | Variable | Value |
  | --- | --- |
  | `{{ contact.first_name }}`, `{{ contact.last_name }}`, `{{ contact.full_name }}` | Contact name |
  | `{{ contact.email }}`, `{{ contact.job_title }}`, `{{ contact.department }}` | Contact details |
  | `{{ company.name }}` | The contact's company |
  | `{{ campaign.name }}` | Campaign name |
  | `{{ portal.url }}` | `PORTAL_URL` |
  | `{{ unsubscribe_url }}` | Sales unsubscribe link (blank for Updates) |

- **Reply-to address** – use a mailbox MyPortal imports (IMAP or Microsoft 365
  mail) so replies become tickets.
- **Business hours** – each recipient's company hours (default, falling back
  to the global hours) or the global hours. See [Business Hours](Business%20Hours.md).
- **Send no earlier than** – optional start time.

## Audience

Every filter you set must match:

- **Companies** – all active companies or selected companies, minus any
  excluded companies, optionally VIP companies only.
- **Company has assets with** – companies with at least one asset that has a
  ticked [checkbox custom field](Asset%20Custom%20Fields.md), such as
  "Bitdefender".
- **Company has an active subscription to** – active or pending-renewal
  subscriptions to any selected product.
- **Contacts** – billing contacts (default) or all active staff, optionally
  narrowed by job title or department text.
- **Also send to / Never send to** – individual addresses.

Saving a campaign keeps it as a draft and shows every recipient, plus anyone
left out (unsubscribed from Sales, on the email blocklist, invalid address or
excluded). *Preview email* renders the email as any recipient will see it and
*Send test to me* emails you a copy.

## Sending

*Send campaign* snapshots the recipients and queues one email each. The
scheduler sends queued emails every minute, but only while the recipient is
inside business hours; the rest wait for the next opening. *Cancel sending*
stops any emails that have not gone yet.

When the SMTP2Go module is enabled each email is sent through the SMTP2Go API
and its delivered, open, click and bounce webhooks update the campaign
results. Open and click tracking must be enabled for the sending domain in
SMTP2Go. Without SMTP2Go the email goes through the SMTP relay without
engagement tracking.

## Replies

Sending never creates tickets. When a contact replies, the mailbox import
matches the reply to the campaign email using its `Message-ID` (or, failing
that, the sender address and subject within 90 days). The first reply opens a
ticket in the *marketing* category with an internal note linking back to the
campaign, and the campaign's recipient list links to that ticket. Later
replies from the same contact are added to that ticket while it is open.
