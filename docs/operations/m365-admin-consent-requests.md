# Admin consent request notifications

The `bp_admin_consent_requests` best-practice check enables Entra's admin
consent request workflow and reviewer emails while retaining all existing
reviewer scopes, request duration and reminder settings.

Notifications use the existing IT contact forwarding baseline: the tenant's
`IT Support` distribution group forwards to
`M365_IT_SUPPORT_EXTERNAL_EMAIL_ADDRESS`, which should be the support ticket
ingestion address. If that group is missing, remediation runs the existing IT
baseline remediation first. Conflicting baseline recipients must be resolved
manually; they are not overwritten.

An Exchange mail contact is not an Entra user. Adding the baseline group as a
reviewer would resolve its members rather than deliver to its SMTP address, so
its external contact alone cannot supply a reviewer identity. Remediation
creates `MyPortal Admin Consent Requests`, a hidden shared mailbox with alias
`myportal-admin-consent`, in the same SMTP domain as the baseline support group.
The mailbox's Entra user is added as a reviewer and its mail is forwarded to the
baseline support group. Sign-in must remain disabled. No administrator role is
granted; authorized administrators continue to approve requests through Entra.

The check needs Graph `Policy.ReadWrite.ConsentRequest` and directory user read
access, plus Exchange recipient management. The consent permission is included
in the provisioning/permission-repair contract and resolved by name from the
tenant's Graph service principal. Existing connections need administrator
consent for this added application permission. Exchange Online licensing is
required for the check.

Repeated remediation reuses the mailbox and does not duplicate reviewer scopes.
It verifies the policy and forwarding settings after writing. Newly provisioned
mailboxes may need directory propagation before their reviewer identity becomes
available; retry remediation when indicated. Existing recipients with a
conflicting name/address are not repurposed.

After deployment, submit an admin consent request from a test user and confirm
that a ticket is created and existing reviewers still receive their emails.
Configuration checks cannot prove delivery through Exchange external forwarding
controls, mail filtering or the ticketing system. If no ticket appears, inspect
Exchange message traces and the tenant's external forwarding policy, then check
the support mailbox's ingestion logs. Request details and approval links reach
the support queue; approval still requires the administrator's authorized Entra
session.

## Reported junk and phishing notifications

The separate `bp_reported_email_support_notifications` check adds the same
`M365_IT_SUPPORT_EXTERNAL_EMAIL_ADDRESS` directly to the Defender alert policies
`Email reported by user as junk` and `Email reported by user as malware or phish`
(including the `phish`/`phishing` name variants). It enables each policy and
email notifications, preserves its existing recipients including role recipients
such as `TenantAdmins`, and verifies the updated settings. Repeated remediation
does not add duplicates. Alert email recipients accept the support SMTP address
directly, so this check does not require another contact or shared mailbox.

These are Defender alert notification emails. The reporting mailbox and the
destination for full user-submitted messages in Outlook are unchanged. Severity,
thresholds, notification throttling and suppression settings remain unchanged.
The check uses the existing Security & Compliance connection and requires alert
policy management access. It reports unavailable built-in policies explicitly
instead of creating custom alerts with different detection behavior.

After remediation, report a test junk message and a test phishing message and
confirm the support queue receives both notification emails while existing
recipients still receive theirs. Existing suppression, alert thresholds and
notification throttling can affect when emails are delivered. Review these in
Defender if configuration passes but no notification arrives.
