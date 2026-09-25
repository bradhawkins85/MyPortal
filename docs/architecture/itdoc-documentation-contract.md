# Documentation module scope, permissions, and migration contract

**Status:** Approved baseline for ITDOC 01/12 (#4223)  
**Applies to:** every later ITDOC migration, page, API, search index, export, and integration

## Non-negotiable boundaries

MyPortal's `assets` table and `/assets` page remain the sole asset store and asset
experience. Documentation may add fields or one-to-one/one-to-many records whose
foreign key is `assets.id`; it must not introduce another asset table, copy assets,
or add a competing asset page. Existing numeric asset IDs are canonical.

Documentation content must never contain passwords, API keys, private keys, recovery
codes, tokens, or other authentication secrets. A record may contain a
non-sensitive label or URL identifying an entry in the existing password manager,
but not the secret, a secret-bearing URL, or a value that can retrieve it without
authentication. UI help, imports, API validation, and server-side persistence must
enforce this rule. Hudu's existing password workflow remains separate.

This contract is additive. When documentation is disabled for a company, existing
Assets, knowledge-base, ticket, search, export, audit, and integration behaviour is
unchanged and no documentation navigation, results, or actions are shown.

## Current inventory and extension points

| Area | Existing source of truth | Current path / extension contract |
|---|---|---|
| Companies and access | `companies`, legacy `user_companies`, `roles`, and `company_memberships` | Resolve the active company and effective membership on the server. Reuse role permissions; never trust a submitted company ID. `companies.id` is the tenant key. |
| Assets | `assets` | `app/repositories/assets.py`, the `assets` feature pack, and `/assets`. Extend by FK to `assets.id`; retain `syncro_asset_id`, `tactical_asset_id`, and all existing columns and sync upserts. |
| Asset custom fields | `asset_custom_field_definitions`, `asset_custom_field_values` | Definitions stay global and values remain keyed by `(asset_id, field_definition_id)`. Documentation can render them but must not duplicate or remap them. |
| Ticket links | `tickets`, `ticket_replies`, `ticket_assets`, and `ticket_suggested_assets` | `ticket_assets` remains the canonical ticket-to-asset relation. Documentation-to-ticket links are additive and may not rewrite or infer a replacement for it. Internal replies remain internal. |
| Knowledge base | `knowledge_base_articles`, `knowledge_base_sections`, `knowledge_base_article_users`, `knowledge_base_article_companies`, and `knowledge_base_section_companies` | KB publication and its `permission_scope` continue unchanged. Documentation may link to a KB article by ID; it does not migrate, clone, or silently publish KB content. |
| Audit | `audit_logs` and `app/repositories/audit_logs.py` | Emit create/update/delete, visibility, link, export, and failed-sensitive-content events through the existing audit service. Audit payloads use IDs and field names, not document bodies or secret-like values. Existing audit access remains super-admin-only until an explicit permission change. |
| Hudu | `integration_modules` row `hudu`, encrypted/redacted module settings, `companies.hudu_id`, `app/services/hudu.py`, and Hudu links on existing pages | Keep credentials, settings, company mappings, URLs, contact creation, password creation, and device sync intact. MyPortal documentation and Hudu may be enabled independently and coexist. |
| Feature packs | `app/core/features.py`, feature manifests, and module capability checks | Add a `documentation` pack separately. Global pack availability is necessary but not sufficient: company enablement below must also pass. |

## Proposed additive data map

Names below are reserved for implementation. Migrations must use `CREATE TABLE IF
NOT EXISTS`, additive indexes/FKs, and resumable backfills; destructive renames or
copy-and-swap migrations are out of scope.

| Record | Scope and visibility | Required shape |
|---|---|---|
| `company_documentation_settings` | One row per company; internal configuration | `company_id` PK/FK to `companies.id`, `enabled` default `0`, timestamps and actor. Absence means disabled. This is the per-company kill switch. |
| `documentation_pages` | Exactly one company; internal by default, optionally customer-visible | Stable `id`, mandatory `company_id`, title/body, `visibility` enum (`internal`, `customer`), publication state, optimistic version, creator/updater and timestamps. `customer` plus published is required for customer reads. No secret fields. |
| `asset_documentation` | Company-scoped content attached to an existing asset | Stable `id`, mandatory `asset_id` FK to `assets.id`, page/content metadata and visibility. Tenant is derived by joining `assets.company_id`, not accepted independently from clients. This is **not** an asset table. |
| `documentation_ticket_links` | Internal relationship | Composite uniqueness on `(documentation_page_id, ticket_id)`, FKs only. Both records must resolve to the same non-null company before insert or read. `ticket_assets` is untouched. |
| `documentation_kb_links` | Relationship; effective visibility is the intersection of both records | Composite uniqueness on `(documentation_page_id, article_id)`. Linking never changes KB publication or permission rows. |
| `documentation_password_references` | Metadata only; inherits parent visibility but should normally remain internal | Parent ID plus provider (`hudu` or another already-approved manager), opaque external record ID, label, and ordinary authenticated manager URL. No username, password, token, key, recovery material, or embedded credential. |

All new rows carry stable primary keys. Sync/import provenance, if later required,
uses additive nullable `source_system` and `source_identifier` fields with a unique
company-scoped key. It must never reuse `syncro_asset_id`, `tactical_asset_id`, or
`companies.hudu_id` for a different meaning.

## Server-side authorisation contract

The common order for every HTML route, API, search, relationship resolver, and
export is:

1. Authenticate; unauthenticated non-public requests return `401` (HTML may redirect
   to login). No documentation is anonymously published in this programme.
2. Resolve the effective active-company membership server-side. Super-admin company
   switching uses the existing audited mechanism.
3. Require the global `documentation` feature pack and a settings row with
   `enabled = 1` for the target company. Disabled/missing returns `404` for reads and
   rejects mutations without leaking record existence.
4. Load the target record and derive its company from the stored relationship.
   Never authorise against `company_id` from a URL, form, query, or JSON body alone.
5. Require `menu.documentation` read/write on the effective membership for internal
   access. Customer users additionally need an active membership in that same
   company and may read only records that are both `customer` and published.
6. Apply the same predicate before resolving linked assets, tickets, KB articles,
   password-manager references, or attachments. A link cannot grant access to its
   target. Return `404` rather than confirming an inaccessible cross-company ID.

### Surface rules

| Surface | Mandatory enforcement |
|---|---|
| Pages/direct URLs | Perform the common checks on every list, detail, edit, history, preview, and link route. A hidden menu is not authorisation. |
| APIs | Perform record-level checks for GET/POST/PATCH/DELETE and bulk endpoints. Ignore/reject client tenant fields; validate every ID in a batch atomically. API keys need an explicit documentation scope and remain company-bound. |
| Search/RAG | Filter candidates in SQL/index access by enabled company, membership, publication, and visibility **before** ranking or snippets. Index ACL metadata and remove/tombstone entries when visibility or enablement changes. Never embed secrets. |
| Links | On creation and traversal, authorise both ends and enforce identical company ownership. KB links also apply the existing KB ACL; ticket replies marked `is_internal` never become customer-visible through a link. |
| Exports | Re-run the query ACL at generation and download time; scope exports to one company, exclude internal/unpublished content for customers, use short-lived opaque download references, and audit actor/company/filter/result count. |

Internal technicians and super-admins do not receive implicit cross-company results:
they must select an allowed company context. Background jobs receive an explicit
company ID and apply the same repository predicates.

## Required permission tests

Implementation is not complete until parameterised tests cover each row for both a
browser URL and the equivalent API call (and, where applicable, search/export):

| Actor / request | Expected result |
|---|---|
| Unauthenticated actor | Login redirect for HTML; `401` for API. |
| Member of company A requests an A customer-published record | Allowed with documentation enabled and read permission. |
| Company A member requests an A internal or unpublished record | `404`, unless their effective role has internal documentation read permission. |
| Company A member substitutes a company-B record ID in a direct URL or API call | `404`; no title, link, count, search snippet, or export row leaks. |
| Writer links an A page to a B asset, ticket, or KB target | Atomic rejection (`404`/validation error), no relationship created. |
| Same-company member without `menu.documentation` | `404`/forbidden according to the existing surface convention; never data. |
| Company with absent/disabled settings | Existing routes behave exactly as before; documentation URL/API is unavailable and search/export contains no documentation. |
| Customer export/search | Only same-company, published `customer` records; internal sections and internal ticket replies absent. |
| Authorised internal user | Same-company internal/customer records only; writes additionally require write permission. |
| Super-admin or technician | Allowed only in an explicitly selected effective company context; cross-company link creation still rejected. |

Tests must seed identical numeric IDs where practical to catch missing tenant
predicates, exercise guessed direct URLs rather than navigation alone, and assert
both status and absence of sensitive response text.

## Migration, rollout, and rollback

1. **Baseline/backup:** record counts and checksums for `assets`, asset custom-field
   tables, KB tables, `ticket_assets`, `companies.hudu_id`, and integration settings.
   Back up the database using the normal deployment process.
2. **Expand:** add only the tables/indexes above and the `documentation` feature
   manifest/permissions. Do not update existing rows. Settings default off by
   absence/`enabled = 0`; permissions default deny.
3. **Deploy dark:** deploy code that can read both old behaviour and the new empty
   structures. With the global pack or company switch off, compare existing Assets,
   KB, ticket, search, and Hudu integration tests and responses to baseline.
4. **Pilot:** enable globally, then enable selected companies individually. Any
   optional import writes new documentation IDs while retaining source identifiers;
   it links existing `assets.id`, KB IDs, and ticket IDs rather than recreating them.
5. **Observe:** audit changes and monitor denied cross-tenant requests, indexing,
   export generation, Hudu sync, and integration errors. Expansion migrations must
   be safe to re-run.
6. **Application rollback:** disable the affected company (or global pack), stop new
   jobs, and deploy the prior application. New tables remain dormant. Existing asset
   IDs, custom fields, ticket links, sync identifiers, KB content, Hudu mappings, and
   credentials are unchanged.
7. **Data rollback:** retain new tables for investigation/export. Drop them only in a
   separately approved later migration after retention requirements are met; never
   cascade into an existing table. Do not delete or blank Hudu credentials,
   `companies.hudu_id`, or any integration mapping as part of this programme.

### Release gates

- Disabled-company parity tests pass before every rollout stage.
- Direct-URL and API tenant/visibility tests above pass, including search and export.
- Referential checks find zero cross-company documentation links.
- Pre/post checks prove existing asset IDs and sync identifiers, custom-field rows,
  `ticket_assets`, all KB content/ACL rows, and Hudu settings/mappings are identical.
- Rollback is rehearsed by toggling the company setting off and running the existing
  Assets, ticket, KB, audit, and Hudu suites.

Any later ITDOC design that conflicts with these boundaries must amend and reapprove
this contract before implementation.
