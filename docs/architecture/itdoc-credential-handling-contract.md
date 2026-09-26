# Credential storage and controlled-sharing contract

**Status:** Approved design for ITDOC 13/18
**Dependencies:** ITDOC 01/18 (#4223) and staff/job-title foundations (#4233)
**Roadmap:** ITDOC 00/18 (#4235)

This contract defines the security boundary for organisation-issued onboarding
credentials and deliberately shared operational credentials. It reuses companies,
staff, the `/staff` job-title assignment, portal users, and company memberships.
It does not turn documentation into a password manager and does not store an
employee's privately chosen ongoing password.

## Defaults and invariants

- Every credential item belongs to exactly one `companies.id`. It may additionally
  reference one staff member, asset, or ticket; each reference must resolve to the
  same company. A link provides context, never access.
- Items and secret values are hidden by default. There is no anonymous access,
  blanket company visibility, membership-derived visibility, or manager-role
  visibility. Lists return only item metadata the actor may enumerate and never
  return secret values.
- Secrets must be encrypted at rest with a versioned, externally supplied key and
  authenticated encryption. Ciphertext, nonces, and wrapped data keys are stored
  separately from metadata. Secrets never enter URLs, query strings, logs, audit
  payloads, analytics, notifications, email, tickets, KB/documentation bodies,
  search indexes, snippets, ordinary exports, or customer publication.
- Credential records are a separate repository and content type. Ordinary
  documentation/RAG search, documentation export, full-company export, customer
  publication, and generic relationship traversal exclude them by default. A
  future dedicated vault export requires separate approval, re-authorisation at
  generation and download, and encrypted output.
- No API response includes a secret unless it is the result of an authorised,
  explicit reveal operation. Create and rotate accept a secret write-only and
  return metadata. Edit never redisplays the stored value.
- Reveals are short lived in the UI, are not cached (`Cache-Control: no-store`),
  and are audited using actor/item/grant IDs, result, reason, and UTC timestamp—
  never the value. CSRF, recent authentication, and MFA/step-up policy apply.
- A shared password is possession of a resource, not evidence of an employee's
  identity. Authentication, approvals, signatures, and audit attribution must use
  the employee's own portal/identity-provider session.

## Credential classes and lifecycle

| Class | Intended content and owner | Default grants | Lifecycle and review |
|---|---|---|---|
| `onboarding_password` | Organisation-issued initial or temporary password for exactly one staff identity. Never the employee's later chosen password. | Recipient may reveal until consumed/expired; their explicitly granted manager may reveal for onboarding support. No generic manager grant. | Earliest of first successful use, IdP-reported replacement, explicit invalidation, or a short configured expiry revokes reveal. Where the IdP supports it, set change-at-first-use and replace/invalidate the initial password after first use. Daily stale-item check; owner review within 24 hours of the planned start date. Never recover or ingest the replacement password. |
| `shared_account` | Deliberately shared operational login, not attributable personal access. | None; named staff or company job-title grants only. | Review quarterly and on grantee departure/title change; rotate after revocation, suspected disclosure, or according to owner policy (at most annually). Prefer individual accounts where available. |
| `high_privilege_admin` | Tenant/domain/network/root administrative credential. | None; explicit named or job-title grants, with reveal granted sparingly. | Review monthly and after every external-user or privilege change; rotate at least every 90 days and immediately after revocation/disclosure. Require step-up authentication and a reveal reason. |
| `service_account` | Non-human application or automation credential. | No interactive reveal by default; named maintainers may administer/rotate. Runtime retrieval uses a separately scoped workload identity, never company membership. | Review quarterly; rotate at least every 90 days or use provider-managed short-lived credentials. Rotation records verification and retirement of the prior value. |
| `recovery_item` | Recovery code, break-glass secret, or encrypted recovery material. | None; direct custodians only by default. Job-title access requires explicit per-item approval. | Review monthly and after use; one-time material is marked consumed and replaced immediately. Every reveal requires step-up and a reason. |

Cadences are maximum intervals. A company may configure shorter intervals but may
not silently weaken them. Each item has an accountable owner, next-review and
next-rotation timestamps in UTC, status (`active`, `expired`, `consumed`,
`revoked`), and version. Overdue status never broadens access.

## Records and grant resolution

The implementation is additive and uses stable IDs:

- `credential_items`: mandatory `company_id` and class; encrypted payload
  envelope; label and non-secret username/endpoint metadata; optional same-company
  `staff_id`, `asset_id`, and `ticket_id`; owner, status, review/rotation timestamps,
  optimistic version, and UTC audit timestamps.
- `credential_grants`: exactly one item and exactly one subject: either a direct
  `staff_id`, or a `job_title_id` belonging to that item's company. It stores a
  subset of `enumerate`, `reveal`, `share`, `rotate`, and `administer`, optional
  expiry, grantor, justification, and revoked metadata. `create` and `edit` are
  policy permissions, not delegable item grants.
- Portal identity is resolved through the current portal-user → active company
  membership → staff mapping. A direct grant matches that current staff ID. A
  job-title-derived grant is recalculated from the staff member's current `/staff`
  job title on every request; it is not copied into a standing user grant. Missing,
  inactive, cross-company, expired, or revoked mappings deny access.
- External/customer users follow exactly the same resolution. They can enumerate
  or reveal an item only through an explicit direct staff grant or an explicit
  grant to their **current** job title in that company. Merely being a company
  member, company admin, or a manager is never sufficient.

Grant changes are effective immediately. Revoke ends the selected grant without
deleting audit history. Removing/changing a staff job title immediately removes
the derived access; departure disables direct access. High-privilege and shared
items enter `rotation_required` when a person who could reveal them loses access.

### Separate permission meanings

Permissions are checked independently; none implies another:

| Permission | Meaning |
|---|---|
| `create` | Create an item in an authorised company/class and submit a write-only secret. Does not reveal it afterward. |
| `edit` | Change non-secret metadata and links. Does not reveal, replace, grant, revoke, or rotate. |
| `enumerate` | See an item's non-secret list metadata. Required in addition to any page-level vault access. |
| `reveal` | Decrypt the current active version following step-up controls. Does not permit sharing. |
| `share` | Add a least-privilege, expiring direct or job-title grant no broader than the sharer's delegated authority. Does not reveal. |
| `revoke` | End a grant. It is separately policy-controlled so a responder can revoke without being able to share or reveal. |
| `rotate` | Replace the value, verify the replacement where supported, retire the prior version, and trigger dependent-service handling. Does not reveal the prior value. |
| `administer` | Change owner/class/lifecycle policy or archive the item. It does not imply reveal, share, revoke, or rotate. |

`create`, `edit`, `revoke`, and `administer` come only from explicit vault policy
assigned through the existing role/membership system and are scoped to the active
company. Item grants supply only their stored capabilities. Server-side checks
derive company ownership from the loaded item and referenced records, never solely
from submitted IDs.

## Actor and grant permission matrix

`Allow*` means only in the active company and only when the stated explicit policy
or grant exists. All unlisted combinations deny.

| Actor / basis | Create | Edit | Enumerate | Reveal | Share | Revoke | Rotate | Administer |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Technician, vault policy only | Allow* | Allow* | Deny | Deny | Deny | Allow* | Deny | Allow* |
| Manager, role/membership only | Deny | Deny | Deny | Deny | Deny | Deny | Deny | Deny |
| Employee, own onboarding recipient grant | Deny | Deny | Allow* | Allow* until consumed/expired | Deny | Deny | Deny | Deny |
| Company admin, role/membership only | Deny | Deny | Deny | Deny | Deny | Deny | Deny | Deny |
| Super admin in selected company, vault policy only | Allow* | Allow* | Deny | Deny | Deny | Allow* | Deny | Allow* |
| Service/workload identity | Deny | Deny | Exact item only | Explicit runtime retrieval only | Deny | Deny | Explicit rotation scope only | Deny |
| Impersonated session | Deny | Deny | Deny | Deny | Deny | Deny | Deny | Deny |
| Any current staff user, direct item grant | — | — | As granted | As granted | As granted | — | As granted | As granted |
| Any current staff user, current job-title item grant | — | — | As granted | As granted | As granted | — | As granted | As granted |

Direct and job-title grants have identical capability semantics and neither takes
precedence: effective item capability is their union after company, current-title,
status, and expiry checks. A deny caused by impersonation, inactive membership,
item status, or tenant mismatch overrides that union. A technician or super admin
still needs an item grant to enumerate/reveal/share/rotate; break-glass access is a
separate, time-bound direct grant approved and audited under the same rules.

Consequently, a manager can neither enumerate nor reveal unrelated employees'
onboarding credentials. A named external user cannot enumerate or reveal any
shared or admin item that was not directly granted to them or their current job
title. Guessing an item ID returns `404`, not metadata.

## Operation controls and audit

Create, edit, reveal, share, revoke, rotate, and administer each have distinct API
operations and audit event types. All state-changing browser calls require CSRF;
all calls authenticate, establish non-impersonated context, load the item, derive
its company, resolve fresh staff/title grants, then authorise the single operation.
API schemas mark secret inputs write-only and omit them from examples and errors.

Share validates the target's active same-company staff record/current job title,
capability subset, expiry, and justification atomically. Rotate uses a new encrypted
version, validates it before activating where supported, retires the old version,
and never logs either. Failed decrypt, reveal, cross-tenant reference, or grant
attempts produce sanitised audits and rate-limited responses without confirming an
item outside the actor's enumerable set.

For onboarding items, an IdP callback/poll result may mark `consumed` only when it
is bound to the stored external identity and company. If the IdP cannot report
first use, the configured expiry and a manual invalidate/rotate workflow are
mandatory; the UI must state that first-use invalidation is unconfirmed.

## Required acceptance tests

Parameterised HTML and API tests must cover every matrix row with no grant, direct
grant, current-title grant, stale-title grant, expired grant, wrong company, and
impersonation. They must additionally prove:

1. A manager with the normal manager role cannot list, guess, or reveal another
   employee's onboarding item; only its explicit grant permits it.
2. An external company member cannot list, guess, or reveal an ungranted shared or
   high-privilege item, while a named/direct or current-title grant exposes only
   the selected item's permitted metadata/action.
3. Documentation search/RAG, normal exports, customer publication, ticket/KB
   rendering, email, and linked-record APIs contain neither credential metadata nor
   values. Search indexing queues reject credential record types.
4. Changing a `/staff` job title removes derived access immediately; revoking a
   direct grant removes it immediately and flags applicable items for rotation.
5. Consumed, expired, revoked, and replaced onboarding values cannot be revealed;
   the later employee-selected password is never stored.
6. Each independent permission fails closed, returns a handled `401`/`403`/`404`,
   and emits a value-free audit without a 500 response or existence leak.

## Migration and rollout

Use an expand-only migration with tables absent/dark by default, foreign keys and
same-company validation, uniqueness for one subject per item, and indexes supporting
company/item/subject/status lookups on MySQL and SQLite. Key configuration must be
present before enabling writes; startup must fail the credential feature closed,
not fall back to plaintext. Back up encrypted rows and key metadata separately.

Pilot one company, seed no grants, test the complete matrix, then add individually
approved grants. Rollback disables vault routes/jobs and leaves encrypted tables
dormant; it never exports plaintext or copies values into documentation. Promotion
requires security-owner sign-off, audit review, IdP first-use behaviour verification,
and zero credential results in ordinary search/export/publication probes.
