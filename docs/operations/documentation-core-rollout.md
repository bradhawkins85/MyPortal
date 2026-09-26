# Documentation core release gate and per-company rollout

This runbook is the release gate for ITDOC 12/18. It covers migrations 402–409
and preserves the existing Assets, tickets, knowledge base (KB), Syncro,
Tactical RMM, and optional Hudu foundations. Run it first in a non-production
environment containing a recent, sanitised copy of production-shaped data.

## Safety rules

- Treat `assets.id`, `tickets.id`, and `knowledge_base_articles.id` as canonical.
  Imports link those IDs; they must never create replacement records merely to
  represent documentation.
- Never paste credentials, tokens, Hudu password values, authorization headers,
  or raw integration payloads into evidence. Record only counts, IDs, status,
  timestamps, and sanitised error categories.
- Migrations 402–409 are expand-only. Application rollback leaves their columns
  and tables in place. Do not improvise a destructive database rollback.
- Pilot one company at a time. Access is granted through its existing membership
  and menu permissions; do not broaden global customer access for a pilot.

## 1. Capture the baseline

Take the normal encrypted database backup and record its restore test ID. In a
read-only session, save the result of these checks in the deployment record:

```sql
SELECT COUNT(*) AS assets, COUNT(DISTINCT id) AS unique_assets FROM assets;
SELECT COUNT(*) AS custom_values FROM asset_custom_field_values;
SELECT COUNT(*) AS ticket_asset_links FROM ticket_assets;
SELECT COUNT(*) AS kb_articles FROM knowledge_base_articles;
SELECT COUNT(*) AS kb_company_acl FROM knowledge_base_article_companies;
SELECT COUNT(*) AS hudu_company_mappings FROM companies WHERE hudu_id IS NOT NULL;
SELECT COUNT(*) AS duplicate_syncro_ids FROM (
  SELECT company_id, syncro_asset_id FROM assets WHERE syncro_asset_id IS NOT NULL
  GROUP BY company_id, syncro_asset_id HAVING COUNT(*) > 1
) duplicates;
SELECT COUNT(*) AS duplicate_tactical_ids FROM (
  SELECT company_id, tactical_asset_id FROM assets WHERE tactical_asset_id IS NOT NULL
  GROUP BY company_id, tactical_asset_id HAVING COUNT(*) > 1
) duplicates;
```

Also export ordered `(id, company_id, syncro_asset_id, tactical_asset_id)` asset
identities and the primary keys of custom-field, `ticket_assets`, KB ACL, and KB
content rows to the protected deployment evidence store. Compare these values,
not sensitive content, after each stage.

## 2. Rehearse expansion and rollback

1. Restore the baseline backup into the isolated test database.
2. Run the normal application startup migration path. Run it a second time and
   confirm no migration is reapplied and no duplicate record appears.
3. Run `pytest -q tests/test_documentation_core_gate.py` and the focused existing
   suites listed below.
4. Exercise the full pilot journey: existing asset → KB article → process run →
   existing ticket, then link that asset to an IP address and rack position.
5. Publish and unpublish the article; archive and restore the asset. Confirm the
   original ticket link and custom fields remain intact throughout.
6. Stop documentation/process writes, revoke the pilot company's new menu write
   permission, and deploy the previous application build. Do **not** reverse the
   schema. Confirm legacy Assets, ticket, KB, Syncro/Tactical, and Hudu journeys.
7. Redeploy the candidate and confirm the additive records are still readable.

Required focused suites:

```bash
pytest -q tests/test_documentation_core_gate.py tests/test_asset_relationships.py \
  tests/test_infrastructure_documentation.py tests/test_ticket_asset_link.py \
  tests/test_knowledge_base_feature_pack.py tests/test_asset_source_awareness.py \
  tests/test_asset_importer.py
```

### Required staged browser gate

The lower-level suites above remain mandatory, but they do not approve a staged
promotion on their own. Configure the protected `documentation-staging` GitHub
environment and run **Documentation browser rollout gate** against the deployed
candidate. A missing control, link, or finishable browser flow fails that job.

Prepare two isolated companies using the normal administration screens (never
production data, SQL, or setup APIs), and store a JSON fixture in the protected
`DOCUMENTATION_BROWSER_FIXTURE_JSON` environment secret. It must contain:

- `base_url`; an `empty_search` value; `wrong_company_path` and a harmless
  `wrong_company_marker` used to prove that denial pages do not disclose titles;
- `users` entries for `technician`, `customer`, `named_staff`, `job_title`, and
  `wrong_company`, each with email/password; authorised role entries also need
  `visible_path` and `visible_text` for their assigned published record;
- `asset` with `path`, `name`, `search`, linked `runbook`, active
  `process_template`, and same-company numeric `ticket_id`;
- `website` with `name` and populated TLS/domain observations; and
- `credential_share` with a newly issued fragment URL and separately delivered
  one-time `code`. Create a fresh share before every run because redemption is
  intentionally destructive.

The technician needs documentation, asset-photo, process-run, website and expiry
permissions. The customer sees only deliberately published content. Give the
named-staff and job-title users the same single standing-access test record by
their respective grant types. The wrong-company user belongs only to company B.
Do not place real credentials or customer data in this environment or fixture.

For a local rehearsal, install Chromium and run the exact gate command:

```bash
playwright install chromium
MYPORTAL_BROWSER_FIXTURE=/secure/path/fixture.json \
  pytest -q -m browser_gate tests/browser
```

The gate covers keyboard-only search → asset → runbook, asset → process → ticket,
website → expiry, mobile camera-picker fallback → photo, one-time recipient
redemption, useful empty/expired states, rendered role visibility, and direct
wrong-company requests. Record the workflow URL, commit SHA, UTC run time, target
company and browser in deployment evidence. A skip is not a pass: the protected
workflow deliberately fails before pytest when its fixture secret is absent.

## 3. Security and failure-path gate

Use two companies with similarly named assets and separate customer users.

- As company A, guess company B asset, process-run, ticket, KB, IP, and rack IDs.
  Expect `403` or `404` with no title, count, relationship, or snippet leakage.
- Attempt process and infrastructure links from company A to company B. Expect an
  atomic rejection and zero new relationship rows.
- Submit missing, malformed, oversized, and timezone-naive values. Expect a
  handled 4xx response, not a 500 or exception detail.
- Repeat one Syncro and one Tactical payload. `asset_source_records` must resolve
  to the same canonical asset and asset count must not increase.
- Create an ambiguous serial/name match. Confirm it is quarantined rather than
  linked or duplicated.
- Simulate timeout, authentication rejection, and rate limiting. Confirm a
  completed failed `integration_sync_runs` row contains only a bounded,
  sanitised `safe_error`; no token, header, credential, or payload may appear.
- With Hudu enabled, run its existing connectivity/read journey before and after
  expansion. With Hudu disabled, confirm documentation remains usable and no Hudu
  configuration is required.

Before promotion, these integrity checks must all return zero:

```sql
SELECT COUNT(*) FROM process_runs p JOIN assets a ON a.id=p.asset_id
WHERE p.asset_id IS NOT NULL AND a.company_id<>p.company_id;
SELECT COUNT(*) FROM process_runs p JOIN tickets t ON t.id=p.ticket_id
WHERE p.ticket_id IS NOT NULL AND t.company_id<>p.company_id;
SELECT COUNT(*) FROM ip_addresses i JOIN assets a ON a.id=i.asset_id
WHERE i.asset_id IS NOT NULL AND a.company_id<>i.company_id;
SELECT COUNT(*) FROM rack_equipment r JOIN assets a ON a.id=r.asset_id
WHERE a.company_id<>r.company_id;
SELECT COUNT(*) FROM asset_source_records s JOIN assets a ON a.id=s.asset_id
WHERE s.asset_id IS NOT NULL AND a.company_id<>s.company_id;
```

## 4. Staged production rollout

For each company, record an owner, start time (UTC), baseline evidence ID, rollback
decision maker, and observation window.

1. **Dark deploy:** apply migrations with existing permissions unchanged. Verify
   baseline counts and integrations for all companies.
2. **Internal read:** grant existing documentation/asset read permission only to
   the pilot technicians. Verify direct URLs and searches remain tenant-scoped.
3. **Internal write:** grant write permission, create the journey above using
   existing records, and observe audit and sync failures for one business day.
4. **Customer publication:** deliberately publish selected KB/asset information.
   Test with a real pilot customer role; unpublished, internal, archived, and
   other-company data must remain absent.
5. **Browser gate:** refresh the one-time credential share and run the protected
   browser workflow. Do not promote on any failure, retry, or skipped journey.
6. **Promote or roll back:** compare baseline identities and counts, obtain owner
   sign-off, then proceed to the next company. On any isolation, duplication,
   broken-link, credential exposure, or unexplained 500 event, revoke the pilot
   permissions, stop related writes, preserve evidence, and execute application
   rollback from section 2.

Rollout is complete only when every enabled company has its own sign-off and the
post-rollout snapshot shows unchanged canonical asset IDs and integration IDs,
custom-field rows, ticket links, KB content/ACL rows, and Hudu mappings.
