# Microsoft 365 notification delivery

The **M365 Direct Delivery** module supports two explicit modes. Existing
companies remain on `inbox_item` until an administrator opts a company into
`send_mail`; rollback is the same setting change.

## Inbox item (compatibility mode)

`inbox_item` calls Graph's create-message endpoint for the recipient's Inbox.
Microsoft defines the created message as a **draft**. It has not passed through
Exchange transport, its From/Sender presentation is not proof of sender
authentication, and creating it does not generate normal received-mail
delivery evidence or notifications. History consequently reports **created**,
never delivered. Read state can be observed while Graph can still resolve the
item; a moved/deleted/inaccessible item is **unknown**, not unread.

## Exchange sendMail (opt-in)

Set `delivery_mode` to `send_mail` and configure `sender_address`. The existing
M365 application must already have the catalogued `Mail.Send` application
permission and access to that sender mailbox. MyPortal neither expands mailbox
access nor silently falls back to SMTP in this mode. A successful Graph response
is recorded as **submitted**. Graph does not confirm final delivery.

Inline attachments are bounded to 3 MiB. The operation is issued once: an
ambiguous timeout is not automatically retried because Graph sendMail has no
idempotency key and a retry could duplicate mail. Ticket reply/recipient rows
act as the idempotency record after a terminal `created` or `submitted` result.

Before enabling a company, validate in a sandbox tenant: `isDraft`, displayed
sender, Inbox/Sent Items rendering, desktop/mobile notifications, attachment
rendering, and read-state behavior after moving and deleting the item. Security
and transport policy remain authoritative; this module does not bypass them.
