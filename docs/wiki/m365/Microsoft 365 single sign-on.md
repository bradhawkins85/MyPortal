# Microsoft 365 Single Sign-On

## Overview

Companies that are connected to Microsoft 365 can let their people sign in to MyPortal with their Office 365 (Microsoft Entra ID) account. A linked user chooses **Sign in with Microsoft** on the sign-in page and lands in MyPortal without typing a MyPortal password. When Microsoft has already asked for multi-factor authentication, they are not asked for a MyPortal authenticator code either.

Microsoft sign-in is configured **per company**. Every company starts with it turned off, so nothing changes until an admin opts in.

This page is for MSP and company admins. It explains how to turn the feature on, how accounts are linked, and what to check when someone cannot sign in.

---

## Prerequisites

- The company is connected to Microsoft 365 (a tenant is set on **Edit company → Microsoft 365**, or the company is mapped to a CSP tenant). Microsoft sign-in uses that tenant to decide which Microsoft accounts belong to the company.
- The shared MyPortal sign-in app (the same multi-tenant app described in [PKCE App Setup](PKCE%20App%20Setup.md)) is used. It only asks users for the basic `openid`, `profile` and `email` permissions, so **no extra admin consent is needed** in the customer tenant.
- The company's Microsoft 365 connection can read user account status through Microsoft Graph (`User.Read.All` or equivalent access). MyPortal checks `accountEnabled` against the signing-in user's permanent Microsoft object ID before admitting them. If this check is unavailable, sign-in fails without creating an account.
- People do not need an existing MyPortal account or a pre-created Microsoft link. Eligible users are created or linked on their first Microsoft sign-in.

---

## Modes

Each company has one of three modes:

| Mode | What it means |
|---|---|
| **Off** | Microsoft sign-in is not offered for the company. This is the default. |
| **Optional** | People can sign in with Microsoft or with their MyPortal password. |
| **Required** | People who are linked to a Microsoft account must use Microsoft sign-in. Their password and passkey sign-in are refused and password reset emails are not sent. |

Things to know about **Required**:

- **Unlinked users keep password sign-in.** Only users who already have a linked Microsoft account are forced to use it, so turning on Required never locks out someone who has not been linked yet.
- **Stale links do not lock anyone out.** If a user's linked Microsoft tenant no longer reaches any of their companies with Microsoft sign-in turned on (for example the tenant was disconnected), they keep password sign-in.
- **Super admins are never forced.** They can always sign in with a password or passkey.
- Required can be chosen once the company has a connected Microsoft 365 tenant; no pre-linked user is needed.
- A user whose company requires Microsoft sign-in cannot unlink their own Microsoft account from their profile.
- If a person belongs to several companies, Required on any one of them applies to them.

---

## Turning it on

1. Make sure the company is connected to Microsoft 365.
2. Open **Admin → Companies**, choose the company, then open **Edit company → People & access → Microsoft sign-in**.
3. Pick **Optional** to try it with a few people, or **Required** once everyone who should use it is linked.

The same setting is available through the [Company SSO API](#company-sso-api).

> **Note:** The Edit company page is currently available to super admins only. Company admins who are not super admins manage Microsoft sign-in through the API for now.

---

## How accounts get linked

A MyPortal account is linked to a Microsoft account by Microsoft's permanent tenant and user identifiers, not by email address, so a later change of email or UPN does not break the link.

**Automatic creation or linking on first sign-in.** When someone signs in with Microsoft for the first time, MyPortal checks all of these:

1. **The address is trustworthy.** MyPortal uses the Microsoft email only when Microsoft marks it as verified by the tenant (the `xms_edov` claim). Otherwise it uses the Microsoft sign-in name (UPN). The address must be a single, complete email address.
2. **The domain belongs to the company.** The address's domain is one of the **Email domains** of a company that is connected to that Microsoft tenant and has Microsoft sign-in turned on.
3. **Microsoft sign-in is enabled for the user.** Microsoft Graph must return the same permanent user ID with `accountEnabled=true`. This is checked on subsequent sign-ins too, including accounts linked manually.
4. **An existing account is eligible.** If an account exists, exactly one active MyPortal user in those companies must match, with no existing link to a different Microsoft identity. The user must belong only to companies in that tenant with Microsoft sign-in enabled. Disabled accounts and accounts in unrelated companies are never replaced or moved. Existing roles, including super-admin status, stay unchanged.
5. **A new account joins the approved domain's company.** If no MyPortal account has the address, MyPortal creates an active account, assigns the ordinary **Member** role in that company, and links the Microsoft identity. The account, company access and identity are saved together; a failure saves none of them. New accounts never receive administrator access. No MyPortal password is supplied to the user; Microsoft sign-in continues through the existing MFA flow.

If an eligibility rule fails, nothing is created or linked and the person sees *"This Microsoft account is not eligible to sign in to MyPortal. Contact your administrator."* An admin can still manage links by hand (below). A missing **Member** role or unavailable directory check must be corrected before provisioning can succeed.

**Linked by an admin.** Admins can link, re-link and unlink users:

- On **Edit company → Microsoft sign-in**, each of the company's users shows a status of *Linked · auto*, *Linked · admin* or *Not linked*, with their Microsoft UPN and last Microsoft sign-in. Choose **Link** to search the company's Microsoft directory and pick the right account, or **Unlink** to remove the link.
- Through the Company SSO API (below).

Admin rules:

- Super admins can manage any user.
- Company admins can manage users of their company, but not super admins and not users who also belong to a company outside their management scope.
- A Microsoft account that is already linked to another MyPortal user cannot be linked again; the message names the account that holds it.
- Linking a user who already has a Microsoft link replaces it.
- Users can also see, and when allowed unlink, their linked account on **Profile → Microsoft sign-in**.

---

## MFA behaviour

- **Microsoft already performed MFA** (the sign-in token reports multi-factor authentication): the MyPortal authenticator code is skipped, and so is forced MyPortal two-factor enrolment.
- **Microsoft did not perform MFA and the user has a MyPortal authenticator:** MyPortal asks for the six-digit code. Wrong codes count toward the usual account lockout.
- **Microsoft did not perform MFA and the user has no authenticator:** the user is signed in and MyPortal's normal prompt to set up two-factor authentication applies.

---

## What users see

- The sign-in page always has a **Sign in with Microsoft** button.
- When someone types their work email, MyPortal checks the email's domain only (it never looks up the person). If the company's mode is **Optional**, a **Continue with Microsoft** button appears above the password field. If it is **Required**, the browser goes straight to Microsoft, once per address in each browser tab, together with a **Use my password instead** link for people who have no Microsoft link (and super admins) so they can stay on the page and type their password.
- After a Microsoft sign-in fails, the sign-in page shows the error and does not send the person back to Microsoft automatically.
- A person who is refused password sign-in in Required mode sees *"Your organisation requires Microsoft sign-in."* and the Microsoft button.
- Without JavaScript the Microsoft button and the password form both still work.
- Logging out of MyPortal ends only the MyPortal session, not the Microsoft session.

---

## Troubleshooting

| What the user sees | What it means and what to do |
|---|---|
| *This Microsoft account is not eligible to sign in to MyPortal. Contact your administrator.* | Check the approved company email domain, tenant, existing account's company access, conflicting links and Microsoft `accountEnabled` status. Eligible users are created or linked automatically; pre-linking is unnecessary. |
| *Microsoft sign-in is not enabled for your organisation.* | None of the companies connected to the user's Microsoft tenant has Microsoft sign-in turned on. Set the company's mode to **Optional** or **Required** and check that the correct tenant is connected. |
| *Your organisation requires Microsoft sign-in.* | The company is in Required mode and the user is linked. They must use **Sign in with Microsoft**. If they cannot, unlink them (they then use their password again) or move the company back to **Optional**. |
| *Your Microsoft sign-in session expired. Please try again.* | The sign-in took longer than ten minutes, or the link was reused. Start again from the sign-in page. |
| *Microsoft sign-in was cancelled or failed.* | The user cancelled at Microsoft, or Microsoft returned an error. Details are in the application log. Starting Microsoft sign-in in two tabs at once cancels the first — use one tab. |
| *Invalid authenticator code. Start Microsoft sign-in again.* | Microsoft did not report MFA and the six-digit MyPortal code was wrong. Each attempt needs a fresh Microsoft sign-in; wrong codes count toward the account lockout. |
| *Microsoft sign-in isn't available right now. Use your password or contact your administrator.* | MyPortal has no sign-in app registered with Microsoft yet. A super admin connects Microsoft 365 (which registers the app) or sets `M365_PKCE_CLIENT_ID`; until then people use their password. |

**Returning to sign-in without an explanation.** A callback with missing or invalid state and a sign-in binding cookie returns directly to sign-in with the expiry message and logs `sso_state_rejected`. Check that all web workers use the same `SESSION_SECRET` and that the browser starts sign-in on the hostname used by `PORTAL_URL`, so the binding cookie reaches `/m365/callback`. Token redemption failures log the HTTP status and Microsoft's numeric `AADSTS` codes when provided; use those codes to identify the Microsoft configuration or policy failure. Admin consent alone does not enable a company's Microsoft sign-in mode. Once the company is enabled, eligible users are created or linked automatically.

**Checking a staged deployment.** Before relying on Microsoft sign-in, sign in once with a test account from each tenant and check that Microsoft's ID token carries the `amr` claim (MyPortal skips its own code only when `amr` includes `mfa`) and, where the tenant verifies email, the `xms_edov` claim (without it, automatic linking uses the UPN instead of the email). Also check the `client_id` in the address of the Microsoft sign-in page: it must be MyPortal's own registered app, not the Azure CLI client `04b07795-8542-4ab8-9e00-81f6b0a2c83a`. MyPortal never starts Microsoft sign-in with that fallback client; it shows the "isn't available" message above instead.

---

## Audit events

Microsoft sign-in writes to the audit log:

| Event | When | Details |
|---|---|---|
| `auth.sso.link` | An account is linked, automatically or by an admin | How it was linked, who linked it, the tenant and a hashed Microsoft user id; automatic links include `account_provisioned` to indicate a newly created account |
| `auth.sso.unlink` | A link is removed | Who removed it |
| `auth.sso.fail` | A sign-in is refused for a user MyPortal could identify | A reason: `no_link`, `company_mismatch`, `account_disabled`, `tenant_not_enabled` or `password_login_refused` |
| `auth.login.succeed` | A successful sign-in | `authentication_method` of `m365_sso`, plus `mfa_satisfied_by` of `microsoft`, `myportal_totp` or `none` |
| `company.sso_mode.update` | A company's mode changes | The old and new mode |

The `password_login_refused` reason is also recorded with an `authentication_method` of `password`, `passkey` or `password_reset` to show which sign-in route was refused in Required mode. Failures where MyPortal cannot tell who the user is are written to the application log only, with the email hashed.

---

## Company SSO API

These endpoints are documented in Swagger UI and need an authenticated user who is allowed to manage the company:

- `GET /api/companies/{company_id}/sso` – mode, tenant status and linked identities
- `PUT /api/companies/{company_id}/sso` – set the mode (`off`, `optional` or `required`; any other value is rejected with 422)
- `GET /api/companies/{company_id}/sso/directory?q=` – search the company's Microsoft directory
- `PUT /api/users/{user_id}/sso-identity` – link a user (`{"object_id": "…", "company_id": 1}`)
- `DELETE /api/users/{user_id}/sso-identity` – unlink a user

---

## Known limitations

- The Edit company page is currently super-admin only, so company admins who are not super admins manage Microsoft sign-in through the API.
- Automatic creation requires an approved company email domain, a connected tenant with Microsoft sign-in enabled, an enabled Microsoft user and the **Member** role.
- Only Microsoft 365 is supported (no Google, generic OIDC or SAML), and sign-in cannot be started from Microsoft My Apps.
