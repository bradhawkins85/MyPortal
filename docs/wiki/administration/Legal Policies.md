# Legal Policies

MyPortal publishes three policies at `/legal`:

| Page | Path |
| --- | --- |
| Privacy Policy | `/legal/privacy` |
| Acceptable Use Policy | `/legal/acceptable-use` |
| Terms and Conditions | `/legal/terms` |

They are public (no sign-in needed), linked under the sign-in, registration and
password reset cards, listed in the signed-out sidebar, and reachable by
signed-in users from the document icon in the sidebar footer.

Every policy page ends with a **No warranty** notice: the portal is provided
"as is", without warranty of any kind, and no liability lies with the
developers or contributors of the MyPortal software. Section 11 of the Terms
and Conditions sets this out in full.

Each policy also has an AI section: how AI features process information and
which providers may be used (Privacy Policy section 4), rules for using AI
features such as no prompt injection and checking output before relying on it
(Acceptable Use Policy section 8), and that AI output may be wrong and is used
at the user's own risk (Terms and Conditions section 4). If you enable a
third-party AI provider, make sure the Privacy Policy wording matches it.

## Operator details

| Variable | Purpose | Fallback |
| --- | --- | --- |
| `LEGAL_ENTITY_NAME` | Organisation operating the portal, named in each policy. | `APP_NAME` |
| `LEGAL_CONTACT_EMAIL` | Address for privacy and policy enquiries. | `SMTP_FROM`, otherwise users are asked to raise a ticket |

## Customising the wording

The text lives in `app/templates/legal/_privacy.html`,
`_acceptable_use.html` and `_terms.html`, and describes the information
MyPortal collects and the features it offers. It is written with the
Australian Privacy Principles and the GDPR in mind but is a starting point,
not legal advice: review it with your own adviser and adjust it for the
features you enable. Update `LEGAL_POLICIES_UPDATED` in `app/core/legal.py` when the
wording changes.

## Re-acceptance banner

When a signed-in user's `policies_accepted_version` differs from the current
`LEGAL_POLICIES_UPDATED` value (including when it is `NULL`), a full-width
**policy update banner** appears above the main layout on every page. The
banner reads:

> We've updated our Privacy Policy, Acceptable Use Policy and Terms and
> Conditions (updated {date}). [Review changes] [Accept]

- **Review changes** links to `/legal`.
- **Accept** is a CSRF-protected `POST /api/users/me/policies/accept` that
  sets `policies_accepted_version` to the current value, stamps
  `policies_accepted_at`, writes an audit log entry
  (`user.policies.accept`), and dismisses the banner.
- Works without JavaScript (plain form POST → redirect back to the referring
  page). With JavaScript, the form submission is intercepted and the banner
  is removed in place after a successful `fetch` response.
- The banner only shows on authenticated pages; public pages are unaffected.
- Set `LEGAL_POLICIES_CHANGE_SUMMARY` in `app/core/legal.py` to a short
  sentence describing the changes; it is appended to the banner message and
  shown on the `/legal` overview page.

## Account anonymisation requests

Privacy Policy section 9 and Terms section 12 describe how people ask for
their account to be deleted or anonymised. Records that must be kept
(tickets, invoices, orders) are anonymised rather than deleted.

- **Requesting.** A signed-in user opens **Delete or anonymise my account** on
  the Security tab of their profile, confirms their email address and ticks
  the acknowledgement. This creates a `pending` row in
  `account_anonymisation_requests` (one per user, so re-submitting never
  duplicates it), raises a support ticket, writes an
  `account_anonymisation.request` audit entry and emails the user.
- **Deciding.** Super admins review requests at **Admin → Anonymisation**
  (`/admin/anonymisation`). **Approve and anonymise** runs the anonymisation;
  **Reject** needs a reason, which is emailed to the user. A rejected user can
  submit a new request. Super admins can't decide their own request.
- **Requests by email or phone.** On **Admin → Users**, the **Anonymise**
  action records the request and anonymises the account straight away.
- **What the run does.** It emails the original address a final notice, adds
  the original and placeholder addresses to the sales opt-out list, removes
  the person's content from the RAG index, then, in one transaction, replaces
  their name and email (`anonymised+<id>@invalid`) on the user and linked
  staff rows, redacts their email and phone in ticket and chat text, deletes
  sessions, two-factor, passkeys, sign-in tokens, SMS links, call recordings,
  voicemail attachments and email tracking events, and closes the account.
  Recording and voicemail files are deleted after the transaction commits.
- **Failures.** A failed run leaves the request `approved` with the error
  type noted. Approving it again is safe because every step is idempotent.
- **Audit trail.** Audit entries hold ids and row counts only. The request
  keeps a SHA-256 hash of the original email so future marketing campaigns
  still exclude it.
