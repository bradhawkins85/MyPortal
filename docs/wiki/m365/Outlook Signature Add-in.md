# Outlook Signature Add-in

Microsoft is moving Outlook signatures into the mailbox ("roaming
signatures"). While roaming signatures are active, Exchange Online ignores
signatures written with `Set-MailboxMessageConfiguration`, and Microsoft
offers no API for the roaming signature store. MyPortal therefore supports two
ways to get signatures into Outlook, chosen per company under
**Office 365 → Signature management → How signatures reach Outlook**.

| Delivery method | Tenant setting | How signatures arrive |
| --- | --- | --- |
| **Exchange push** (default) | `PostponeRoamingSignaturesUntilLater = $true` | *Deploy* on a published template writes each selected mailbox's signature through Exchange Online. |
| **Outlook add-in** | `PostponeRoamingSignaturesUntilLater = $false` | The MyPortal add-in inserts the signer's current primary signature whenever they start a new message or meeting invite. Nothing needs deploying. |

Saving the delivery method runs `Set-OrganizationConfig` for the whole tenant
and is recorded in the audit log (`m365.signatures.delivery_mode`). Admins can
switch back at any time. The Deploy page warns when the tenant setting no
longer matches the chosen method and offers to fix it.

Classic Outlook sync through the tray agent is independent of this choice.

## Setting up the add-in

Prerequisites: Microsoft 365 is connected for the company, and `PORTAL_URL` is
the portal's public HTTPS address (Outlook loads the add-in from it).

1. **Prepare the app registration.** MyPortal updates the company's own
   MyPortal app in Microsoft Entra using `Application.ReadWrite.OwnedBy`:
   - Application ID URI `api://<portal host>/<client id>`
   - delegated scope `access_as_user`, with v2 access tokens
   - SPA redirect `brk-multihub://<portal host>` for nested app authentication
   - web redirect `<portal>/m365/signatures/addin/consent` for admin consent
2. **Grant admin consent** as a Global Administrator, so staff are never
   prompted. The add-in runs without UI and cannot ask for consent itself.
3. **Download the manifest** and upload it in the Microsoft 365 admin centre
   under *Settings → Integrated apps → Upload custom apps → Office Add-in*.
   Assign it to the users who should get MyPortal signatures. The manifest id
   is fixed per company, so uploading a newer manifest updates the add-in
   instead of adding a second copy.

## How it works

- Outlook loads `/outlook-addin/<id>/launch.html` (browser runtime) or
  `/outlook-addin/<id>/launch.js` (Classic Outlook's JavaScript-only runtime).
  The script bundles the vendored MSAL.js, the company's client id and the
  event handlers.
- On `OnNewMessageCompose` / `OnNewAppointmentOrganizer` the add-in gets a
  token silently with nested app authentication and calls
  `GET /api/outlook-addin/signature` with `Authorization: Bearer <token>`.
- MyPortal checks the token signature against the tenant's published keys,
  the issuer, audience and authorised party (the company's app), the
  `access_as_user` scope, and that the tenant is the company's tenant. It maps
  `preferred_username` to an active staff record on a company email domain
  and returns their primary signature, rendered like Classic Outlook sync.
- `204` means there is nothing to insert (the company uses Exchange push, or
  no active signature applies). Any failure leaves the message untouched.
- The endpoint is limited to 300 requests a minute per client IP.

## Checking it

Open Outlook on the web as a staff member and start a new message. Their
signature appears in the body. If it does not:

- confirm a published, active signature applies to them and their address
  matches their staff record;
- check *How signatures reach Outlook* shows the add-in setup as **Ready**;
- allow up to 24 hours after uploading the manifest for Outlook to load it.

Outlook on the web, the new Outlook for Windows and Outlook for Mac load the
add-in from the portal's own origin. Classic Outlook for Windows runs it in a
JavaScript-only runtime; on managed devices the tray agent's Classic Outlook
sync remains the dependable option there.
