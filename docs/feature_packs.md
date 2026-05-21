# Feature packs

A **feature pack** is a self-contained area of MyPortal (e.g.
`tickets`, `knowledge_base`, `backups`) that can be loaded, unloaded,
and reloaded at runtime without restarting the rest of the
application. Reloading the `tickets` pack does not affect users
working in backups, the knowledge base, or any other pack.

Feature packs are the in-process counterpart to the deploy-time
zero-downtime upgrade flows in
[`zero_downtime_upgrades.md`](zero_downtime_upgrades.md). Use packs
for routine code updates inside one area; use the deploy flows for
changes that touch the FastAPI app object itself, middleware, Python
dependencies, or the framework.

## Authoring a pack

Each pack lives at `app/features/<slug>/__init__.py` and exposes a
module-level `PACK` attribute that is an instance of
`app.core.features.FeaturePack`:

```python
# app/features/tickets/__init__.py
from fastapi import APIRouter
from app.core.features import FeaturePack

from app.features.tickets import routes  # noqa: F401  side-effecty

router = APIRouter(prefix="/tickets", tags=["Tickets"])
# ... add routes to ``router`` ...

async def _startup() -> None:
    """One-shot startup work (warm caches, register subscribers)."""

async def _shutdown() -> None:
    """Reverse of _startup; called before unload removes the routes."""

async def _scan_inbox() -> None:
    while True:
        # ... do work ...
        await asyncio.sleep(60)

PACK = FeaturePack(
    slug="tickets",
    version="1.4.2",
    routers=(router,),
    startup=_startup,
    shutdown=_shutdown,
    background_jobs=(_scan_inbox,),
)
```

### Fields

| Field             | Required | Purpose                                                             |
| ----------------- | -------- | ------------------------------------------------------------------- |
| `slug`            | yes      | URL-safe identifier; must match the directory name.                 |
| `version`         | no       | Human-readable version surfaced in logs and `/api/features`.        |
| `routers`         | no       | FastAPI `APIRouter` instances mounted under a per-pack parent.      |
| `startup`         | no       | Async callable invoked after the routers are mounted.               |
| `shutdown`        | no       | Async callable invoked before the routers are removed.              |
| `background_jobs` | no       | Tuple of zero-arg coroutine factories started via `create_task`.    |

Background jobs are cancelled when the pack is unloaded or reloaded.
If a job must fire on only one instance in a multi-instance
deployment, wrap it with `singleton_run` from
`app/services/singleton_jobs.py`:

```python
from app.services.singleton_jobs import singleton_run

@singleton_run("tickets_scan_inbox", ttl_seconds=300)
async def _scan_inbox() -> None:
    ...
```

## Loading and reloading

Packs to load on startup are listed in the `FEATURE_PACKS`
environment variable (comma-separated slugs). They load after the
database is connected and migrations have run.

Reload a single pack at runtime:

```http
POST /api/features/tickets/reload
```

The endpoint is super-admin only and CSRF-protected. The response
includes the new `version`, `loaded_at`, and `last_reload_duration_ms`.
If the new code fails to import or mount, the previous instance stays
mounted, `last_error` is populated, and the call returns 200 with the
previous version (or 500 if there is no previous instance).

List the currently loaded packs:

```http
GET /api/features
```

### Pack versioning and `system_update`

The `version` field is more than a diagnostic: the `system_update`
system automation in `app/services/scheduler.py` uses it to decide
whether a pending GitHub update can be applied without restarting the
application.

When `system_update` runs and the remote `main` branch is ahead of the
local checkout, the scheduler diffs the incoming changes. If **every**
changed file lives under `app/features/<slug>/…` for one or more
currently-loaded packs **and** each affected pack's `PACK.version`
literal in the incoming `__init__.py` differs from the running
version, the scheduler:

1. Fast-forwards the working tree to the new commit
   (`git merge --ff-only`).
2. Calls `FeatureRegistry.reload(slug)` for each affected pack,
   re-importing it from disk with no downtime.

If any of these conditions are not met — changes outside
`app/features/<slug>/`, a touched pack that is not currently loaded,
an unchanged version literal, a non-fast-forward history, or a reload
that raises — the scheduler falls back to the existing full-restart
flag-file path (`var/state/system_update.flag` ⇒ `scripts/upgrade.sh`).

For this optimisation to apply you must therefore:

* Keep `version` as a **string literal** inside the `PACK = FeaturePack(...)`
  declaration in `app/features/<slug>/__init__.py` (it is parsed
  textually from the file at the incoming git ref).
* Bump `version` on **every** change to the pack, however small. Treat
  it the same way you would a published library version.

The optimisation is bypassed when a user clicks **Restart now** in the
admin UI (which calls `run_now`, setting `force_restart=True`), so an
operator can always demand a full restart.

## Reload safety contract

The loader guarantees:

* **Atomic swap.** The new router is mounted before the old one is
  detached, so a client never sees an unmounted period.
* **Per-pack serialisation.** Two concurrent reload requests for the
  same pack are serialised by an `asyncio.Lock`; reloads of
  *different* packs run in parallel.
* **In-flight draining.** The loader counts active requests against
  the old router. After the swap it waits up to 10 seconds for those
  requests to finish before tearing the router down. Long-running
  requests that exceed the window are logged but not interrupted.
* **Module purge.** `sys.modules` entries under `app.features.<slug>`
  are dropped before re-import so the new version really is fresh.
* **Failed reloads are no-ops.** Any exception during import or mount
  is recorded on the (existing) state and the previous code keeps
  serving.

## What hot-reload cannot do

* Reload code outside `app/features/<slug>/`. Changes to `app/main`,
  middleware, the FastAPI app, or shared services need a full
  graceful reload (see [`zero_downtime_upgrades.md`](zero_downtime_upgrades.md)).
* Reload Python dependencies (`pyproject.toml`). A wheel that was
  already imported cannot be un-imported.
* Make destructive database migrations safe. Follow the
  [expand/contract policy](zero_downtime_upgrades.md#expandcontract-migration-policy)
  whether you reload via a pack or via a deploy.
* Recover from a TypeError in a route's signature at the moment of
  import. The new code fails to load and the previous version keeps
  serving — fix the bug and retry.

## Migration discipline

Packs may ship their own SQL migrations alongside the global
`migrations/` directory. All migrations — pack or otherwise — must be
**idempotent and additive** so old workers, new workers, and other
loaded packs can read the schema simultaneously during a deploy or
reload. The migration runner is invoked once on startup and is
intentionally not invoked again on pack reload; changes to schema
should ship in a normal release rather than via a single-pack hot
reload.

## Testing a pack

There is a reference test pack at `app/features/_example_pack/` and a
loader test suite at `tests/test_feature_registry.py` that pack
authors can copy. The recommended pattern is:

1. Write unit tests for the pack's services as you would today.
2. Add an end-to-end test that loads the pack via
   `FeatureRegistry.load("your_pack")`, drives a `TestClient` against
   it, and asserts the routes behave.
3. Add a reload test that calls `reload("your_pack")` and asserts the
   pack still works (the loader does this generically; a per-pack
   smoke test is still cheap insurance).

## Admin UI

Super admins can manage packs from **Admin → Feature Packs**
(`/admin/feature-packs`). The page lists every loaded pack with its
current version, last-loaded timestamp, in-flight request count, last
reload duration, and last error (if any). The **Reload** button on
each row issues `POST /api/features/{slug}/reload` and reloads the
page once the API responds so the table reflects the new state.

The page is sourced from `feature_registry.list()` and does not perform
its own DB queries, so opening it has negligible overhead even when
many packs are loaded.

## Dev-only auto-reload

Setting `FEATURE_PACK_WATCH=true` starts a per-pack `watchfiles`
watcher when the application boots. Saving any file under
`app/features/<slug>/` (other than `__pycache__`, `.pyc`, editor swap
files, and dot-prefixed files) triggers a debounced reload of just
that pack — equivalent to clicking **Reload** in the admin UI. The
debounce window is 500 ms so an IDE "save all" that touches several
files only fires one reload per pack.

**Leave this off in production.** Production code arrives via
`scripts/upgrade.sh`, which exercises the explicit reload API or a
full graceful restart depending on what changed; a watcher would only
add overhead and surprise. If you forget and `watchfiles` is not
installed, the watcher logs an error and disables itself rather than
crashing the app.

## Migration status

| Pack             | Owner of                  | Notes                                               |
|------------------|---------------------------|-----------------------------------------------------|
| `tickets`        | Portal `/tickets/*`       | Public-portal ticket list, create, detail, replies. Admin `/admin/tickets/*` routes still live in `app/main.py` and will move in a follow-up PR. |
| `service_status` | Portal `/service-status`  | Public-portal service status dashboard. Admin `/admin/service-status*` routes still live in `app/main.py` and will move in a follow-up PR. |
| `notifications`  | `/notifications*`         | Notifications dashboard and settings pages (`/notifications`, `/notifications/settings`). Notifications API routes continue to be mounted from `app/api/routes/notifications.py`. |
| `knowledge_base` | `/knowledge-base*`, `/admin/knowledge-base*` | Portal article catalogue/detail pages plus admin knowledge-base list/editor pages. Knowledge Base API routes continue to be mounted from `app/api/routes/knowledge_base.py`. |
| `chat`           | `/chat*`                  | Chat room list (`/chat`) and individual room view (`/chat/{room_id}`). Chat API routes continue to be mounted from `app/api/routes/chat.py`. |
| `automations`    | `/admin/automations*`     | Admin automations dashboard, create/edit pages, and state/execute/delete actions (`/admin/automations*`). Automations API routes continue to be mounted from `app/api/routes/automations.py`. |
| `reports`        | `/reports/company-overview*`, `/admin/reports/pdf-cover-image*` | Company overview report pages plus PDF cover-image admin management routes. Reporting query-builder routes (`/reporting*`) remain in `app/main.py` for follow-up migration. |
| `backups`        | `/admin/backup-jobs*`, `/admin/backup-summary*` | Admin backup job list/create/edit/delete pages and backup summary dashboard. Backup status webhook API route (`/api/backup-status`) continues to be mounted from `app/api/routes/backup_jobs.py`. |

Each follow-up PR migrates one area (kb, backups, m365,
xero, …) from `app/main.py` into `app/features/<slug>/`. URLs and
dependency injection are preserved across each move; the only
externally visible change is that the area becomes reloadable.
