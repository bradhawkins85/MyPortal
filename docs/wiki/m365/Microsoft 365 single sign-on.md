# Microsoft 365 Single Sign-On

## Overview

Companies that are connected to Microsoft 365 can let their people sign in to MyPortal with their Office 365 (Microsoft Entra ID) account. A linked user chooses **Sign in with Microsoft** on the sign-in page and lands in MyPortal without typing a MyPortal password. When Microsoft has already asked for multi-factor authentication, they are not asked for a MyPortal authenticator code either.

Microsoft sign-in is configured **per company**. Every company starts with it turned off, so nothing changes until an admin opts in.

This page is for MSP and company admins. It explains how to turn the feature on, how accounts are linked, and what to check when someone cannot sign in.

---

## Prerequisites

- The company is connected to Microsoft 365 (a tenant is set on **Edit company → Microsoft 365**, or the company is mapped to a CSP tenant). Microsoft sign-in uses that tenant to decide which Microsoft accounts belong to the company.
- The shared MyPortal sign-in app (the same multi-tenant app described in [PKCE App Setup](PKCE%20App%20Setup.md)) is used. It only asks users for the basic `openid`, `profile` and `email` permissions, so **no extra admin consent is needed** in the customer tenant.
- The people who will sign in already have MyPortal accounts. MyPortal does not create accounts from Microsoft sign-in.

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
- **Super admins are never forced.** They can always sign in with a password or passkey.
- Required can only be chosen once the company has a connected Microsoft 365 tenant and at least one linked user.
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

**Automatic link on first sign-in.** When someone signs in with Microsoft for the first time and their verified Microsoft email matches **exactly one** MyPortal user in the tenant's Microsoft-sign-in-enabled companies, MyPortal links the two accounts and signs them in. If no user matches, several users match, or the matching user is already linked to a different Microsoft account, nothing is linked and the person sees *"No MyPortal account is linked to this Microsoft account. Contact your administrator."*

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
- When someone types their work email, MyPortal checks the email's domain only (it never looks up the person). If the company's mode is **Optional**, a **Continue with Microsoft** button appears above the password field. If it is **Required**, the browser goes straight to Microsoft.
- A person who is refused password sign-in in Required mode sees *"Your organisation requires Microsoft sign-in."* and the Microsoft button.
- Without JavaScript the Microsoft button and the password form both still work.
- Logging out of MyPortal ends only the MyPortal session, not the Microsoft session.

---

## Troubleshooting

| What the user sees | What it means and what to do |
|---|---|
| *No MyPortal account is linked to this Microsoft account. Contact your administrator.* | No MyPortal user matched, more than one matched, the account belongs to a different company, or the account is disabled. Link the user manually on **Edit company → Microsoft sign-in**, or correct the email on their MyPortal account so it matches their Microsoft email, then ask them to try again. The audit log shows the exact reason (below). |
| *Microsoft sign-in is not enabled for your organisation.* | None of the companies connected to the user's Microsoft tenant has Microsoft sign-in turned on. Set the company's mode to **Optional** or **Required** and check that the correct tenant is connected. |
| *Your organisation requires Microsoft sign-in.* | The company is in Required mode and the user is linked. They must use **Sign in with Microsoft**. If they cannot, unlink them (they then use their password again) or move the company back to **Optional**. |
| *Your Microsoft sign-in session expired. Please try again.* | The sign-in took longer than ten minutes, or the link was reused. Start again from the sign-in page. |
| *Microsoft sign-in was cancelled or failed.* | The user cancelled at Microsoft, or Microsoft returned an error. Details are in the application log. |

---

## Audit events

Microsoft sign-in writes to the audit log:

| Event | When | Details |
|---|---|---|
| `auth.sso.link` | An account is linked, automatically or by an admin | How it was linked, who linked it, the tenant and a hashed Microsoft user id |
| `auth.sso.unlink` | A link is removed | Who removed it |
| `auth.sso.fail` | A sign-in is refused for a user MyPortal could identify | A reason: `no_link`, `company_mismatch`, `account_disabled`, `tenant_not_enabled` or `password_login_refused` |
| `auth.login.succeed` | A successful sign-in | `authentication_method` of `m365_sso`, plus `mfa_satisfied_by` of `microsoft`, `myportal_totp` or `none` |
| `company.sso_mode.update` | A company's mode changes | The old and new mode |

The `password_login_refused` reason is also recorded with an `authentication_method` of `password`, `passkey` or `password_reset` to show which sign-in route was refused in Required mode. Failures where MyPortal cannot tell who the user is are written to the application log only, with the email hashed.

---

## Company SSO API

These endpoints are documented in Swagger UI and need an authenticated user who is allowed to manage the company:

- `GET /api/companies/{company_id}/sso` – mode, tenant status and linked identities
- `PUT /api/companies/{company_id}/sso` – set the mode (`off`, `optional` or `required`)
- `GET /api/companies/{company_id}/sso/directory?q=` – search the company's Microsoft directory
- `PUT /api/users/{user_id}/sso-identity` – link a user (`{"object_id": "…", "company_id": 1}`)
- `DELETE /api/users/{user_id}/sso-identity` – unlink a user

---

## Known limitations

- The Edit company page is currently super-admin only, so company admins who are not super admins manage Microsoft sign-in through the API.
- Accounts are never created by Microsoft sign-in; each person needs an existing MyPortal user.
- Only Microsoft 365 is supported (no Google, generic OIDC or SAML), and sign-in cannot be started from Microsoft My Apps.
