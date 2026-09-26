# IT documentation role-permission review matrix

## Decision model and compatibility

This review covers every merged PR from #4237 through the repository head, #4301. A request is allowed only when its active-company role has the feature permission at the required level **and** all existing company, publication, recipient, credential-grant, strong-authentication, and record-level checks pass. Customer-published content remains controlled by `content.knowledge_base` and `content.assets` plus its role audience; those keys do not grant technician actions.

New and fully reviewed roles store every catalogue key. Missing keys therefore default to `none`. For sparse roles saved before this change, the compatibility map preserves only access the same role already exercised: `menu.assets` maps to documentation search, processes, expirations, websites, asset photos and relationships; `menu.network_devices` maps to IPAM and Racks; `menu.continuity` maps to BCP asset links; and `menu.admin.company` maps to credential management/sharing. An explicit `none` always wins. Saving a role materialises the complete selection and ends compatibility fallback. This prevents privilege expansion while avoiding a surprise outage for an existing role.

“View as role” installs the selected role as the effective membership. Page, menu and mutation checks must use that membership; it must not inherit the viewing administrator's super-admin bypass. Public credential shares are grant-token workflows, not role simulation.

## PR-by-PR review

| PR | New surface/action | Previous guard | Permission and level | Enforcement points |
|---|---|---|---|---|
| #4237 | Documentation contract only | N/A | Policy baseline | Architecture documentation |
| #4244 | Asset documentation fields/pages | `menu.assets` | `menu.assets` read/write | menu, page, forms, API, company record lookup |
| #4248 | Typed asset relationships | asset write | `menu.asset_relationships` read/write | detail projection, create/delete endpoints, target permission and company checks |
| #4250 | Runbook lifecycle/attachments | knowledge-base policy | `menu.knowledge_base` read/write plus publication/audience | pages, APIs, attachment/download lookup |
| #4251 | Documentation search API | authenticated user plus result filtering | `menu.documentation_search` read | menu, `/documentation-search`, `/api/documentation/search`, per-result source policy |
| #4252 | Process templates and runs | `menu.assets` | `menu.processes` read/write | menu, pages, all process API reads/mutations, company lookup |
| #4253 | Website/certificate model and monitoring facts | `menu.assets` | `menu.websites` read/write | website APIs, worker operates only on stored authorised configuration |
| #4254 | Expiration aggregation/reminders | `menu.assets` | `menu.expirations` read/write | menu, page, settings/reminder actions, accessible-source filtering |
| #4255 | IP addresses and racks | `menu.network_devices` | `menu.ipam` and `menu.racks` independently, read/write | separate menus/pages/create/delete APIs and company predicates |
| #4256 | Source-aware integrations | asset write/scheduler | owning feature write; worker uses stored company scope | ingestion/reconciliation APIs, worker company keys |
| #4257 | Customer publication/export | source permissions | `content.knowledge_base` / `content.assets` read, never technician write | menus, direct pages, result filtering, attachments and exports |
| #4258 | Rollout/security validation | existing policies | no new grant | regression and rollout checks |
| #4259 | Credential framework | company admin | `menu.credentials` read/write and `menu.credential_sharing` read/write | vault APIs plus existing feature flag/company/grant controls |
| #4260 | Encrypted credential storage | company admin | `menu.credentials` write | create/rotate/reveal/lifecycle APIs; no-store, audit and strong-auth rules |
| #4261 | Technician credential management | company admin | `menu.credentials` read/write | metadata list and mutations, active company |
| #4262 | Technician secure sharing | company admin | `menu.credential_sharing` write | grant create/revoke APIs plus recipient validation |
| #4263 | External credential access | signed grant | grant token, code, expiry and one-time reveal (not portal content role) | public verify/reveal endpoints |
| #4264 | Credential storage/sharing completion | mixed vault guards | the two credential keys plus immutable grant checks | vault API family |
| #4268 | Manual asset creation | asset write | `menu.assets` write | page/form/API and active company |
| #4280 | Asset photo capture/download | asset write/customer-visible asset | `menu.asset_photos` read/write plus `menu.assets` read | detail projection, upload/update/delete, original/thumbnail download |
| #4281 | BCP asset links | BCP edit plus asset visibility | `menu.bcp_asset_links` read/write plus BCP RBAC and `menu.assets` read | BCP pages, create/update workflow, export, company-scoped link repository |
| #4282 | Complete credential flow | company admin | `menu.credentials` write / `menu.credential_sharing` write | vault APIs and UI actions |
| #4283 | Workflow credential creation | workflow operator | `menu.credentials` write plus workflow permission | validated workflow action; secret-source and company checks retained |
| #4285 | Workflow credential sharing | workflow operator | `menu.credential_sharing` write plus workflow permission | workflow action and immutable recipient/grant validation |
| #4286 | Documentation search navigation | any authenticated user | `menu.documentation_search` read | sidebar and direct page/API |
| #4287 | Runbook editor | knowledge-base editor | `menu.knowledge_base` write | editor lifecycle, attachment and export actions |
| #4289 | Split IPAM/Rack pages | shared network-device guard | `menu.ipam` vs `menu.racks` | distinct sidebar entries, page and mutation helpers |
| #4290 | Relationship record picker | asset write | `menu.asset_relationships` write plus target feature read | picker result population and create endpoint |
| #4291 | Process web UI | asset guard | `menu.processes` read/write | sidebar, pages and UI actions use same API decision |
| #4292 | Hardened photo gallery/upload | asset guard | `menu.asset_photos` read/write | gallery, file download and every mutation; MIME/size/idempotency remain enforced |
| #4294 | Website scheduler | stored configuration | `menu.websites` write to configure; no interactive role for worker | queue configuration and manual check; company-scoped job payload |
| #4295 | Website web UI | asset guard | `menu.websites` read/write | sidebar, pages, save/check and API CRUD |
| #4296 | External share landing page | opaque token | immutable credential grant, not a role permission | verify/reveal endpoints; enumeration-resistant response |
| #4297 | Integration health/reconciliation | asset guard | `menu.assets` read/write | direct page and reconciliation actions |
| #4298 | Company vault rollout control | super admin | remains super-admin-only | feature status/change API, audit |
| #4299 | Website insert fix | unchanged | `menu.websites` write | create API and company-scoped insert identifier |
| #4300 / issue #4288 | Role-audienced published content | broad published flag | `content.knowledge_base` / `content.assets` read plus audience | role editor overview, page/search/download/export; separate from technician and vault keys |
| #4301 | Browser rollout journeys | test-only | verifies effective decisions | browser journey gate |

## Regression expectations

Tests must exercise no-access, read-only, read/write, direct URL/API, file download, cross-company identifiers, and role simulation. Credential tests additionally retain recipient, immutable-version grant, expiry, one-time reveal and strong-authentication failures. BCP tests retain plan viewer/editor/approver/admin RBAC in addition to the asset-link key.
