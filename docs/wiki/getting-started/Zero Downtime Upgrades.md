# Zero-downtime upgrades

MyPortal supports two upgrade strategies that keep the public site
reachable throughout a deploy:

1. **Single-server graceful reload (Track B1).** Uvicorn workers cycle
   on `SIGHUP` while nginx rides over the brief reload using
   `proxy_next_upstream`. Recommended default.
2. **Two-instance rolling deploy (Track B2).** Two MyPortal processes
   (`myportal@blue` and `myportal@green`) sit behind a single nginx
   upstream and share one database. `scripts/upgrade.sh --rolling`
   drains and upgrades each side in turn.

For pulling fresh code into a *single area* of the app (tickets,
knowledge base, …) without restarting anything, see
[`feature_packs.md`](feature_packs.md). The two systems are
complementary: feature packs eliminate the need to restart for routine
code changes, and the strategies in this document handle the cases
hot-reload cannot cover (middleware, dependency upgrades, Python
itself).

## Health endpoints

Both upgrade strategies depend on two endpoints exposed by the app:

| Path       | Purpose            | When to use                                              |
| ---------- | ------------------ | -------------------------------------------------------- |
| `/healthz` | Liveness probe     | systemd/k8s liveness, nginx active health checks         |
| `/readyz`  | Readiness probe    | Rolling-deploy gating, "is this worker accepting work?"  |

`/healthz` is cheap and always returns 200 while the event loop is
responsive. `/readyz` only returns 200 after the database is reachable
*and* every loaded feature pack is healthy; until then it returns 503
with a per-check breakdown. The legacy `/health` endpoint remains for
backwards compatibility.

## Track B1 — Single-server graceful reload

### Prerequisites

* The systemd unit declares `ExecReload=/bin/kill -s HUP $MAINPID`
  (the unit shipped in `docs/systemd-service.md` already does).
* Uvicorn is invoked with `--workers N` (N ≥ 2). Single-worker
  deployments still upgrade successfully but will see a small
  connection-refused window during the cycle.
* The nginx config at `deploy/nginx/myportal.conf` is installed; it
  contains `proxy_next_upstream` + `proxy_next_upstream_tries 2` so
  idempotent requests that land mid-cycle are retried automatically.

### Upgrading

```bash
sudo -u myportal /opt/myportal/scripts/upgrade.sh --graceful
```

What the script does:

1. `git pull` and `pip install -e .` in the existing venv.
2. Runs the migration runner (idempotent; happens automatically at
   startup as well).
3. Invokes `systemctl reload myportal.service`, which sends `SIGHUP`.
   Uvicorn starts new workers loaded with the new code, then drains
   the old workers.
4. Polls `http://127.0.0.1:8000/readyz` (override with
   `MYPORTAL_READYZ_URL`) for up to `MYPORTAL_READY_TIMEOUT` seconds
   (default 60) before reporting success.

If the readiness probe fails the script exits non-zero; the previous
workers usually remain alive long enough for nginx to keep serving
while you investigate.

### Constraints

* **Additive migrations only.** Old workers must be able to keep
  reading rows their version doesn't know about. Destructive changes
  (drop column, narrow type) require the [expand/contract
  workflow](#expand-contract-migration-policy) or a maintenance window.
* **Dependency upgrades** that change Python ABI (e.g. swapping
  cryptography wheels) usually require a full `systemctl restart`
  rather than a graceful reload. Plan those as scheduled work.

## Track B2 — Two-instance rolling deploy

Use this when:

* A single instance can't be safely cycled (long-running websocket
  sessions, heavy in-process caches).
* You want the option of pinning new code to one instance and
  observing it before rolling it out everywhere.

### One-time setup

1. Install the templated systemd unit:

   ```bash
   sudo cp deploy/systemd/myportal@.service /etc/systemd/system/
   sudo systemctl daemon-reload
   ```

2. Create a per-instance env file pinning the listen port:

   ```bash
   cat <<'ENV' | sudo tee /etc/myportal.blue.env
   MYPORTAL_INSTANCE_PORT=8001
   ENV
   cat <<'ENV' | sudo tee /etc/myportal.green.env
   MYPORTAL_INSTANCE_PORT=8002
   ENV
   ```

3. Enable both services:

   ```bash
   sudo systemctl enable --now myportal@blue.service
   sudo systemctl enable --now myportal@green.service
   ```

4. Replace `deploy/nginx/myportal.conf` with
   `deploy/nginx/myportal-bluegreen.conf`:

   ```bash
   sudo cp deploy/nginx/myportal-bluegreen.conf \
        /etc/nginx/sites-available/myportal.conf
   sudo touch /etc/nginx/myportal-bluegreen.state
   sudo chown myportal:myportal /etc/nginx/myportal-bluegreen.state
   ```

   Uncomment the `include /etc/nginx/myportal-bluegreen.state;` line
   inside the `upstream` block — the upgrade script writes this file
   to flip an instance to `down` during draining.

### Upgrading

```bash
sudo -u myportal /opt/myportal/scripts/upgrade.sh --rolling
```

What the script does per instance (blue then green by default):

1. Writes `server 127.0.0.1:<port> down;` into the state file and
   reloads nginx. New requests go only to the other instance.
2. Sleeps `MYPORTAL_DRAIN_SECONDS` (default 5s) so in-flight requests
   on the drained instance can finish.
3. `systemctl restart myportal@<instance>.service`.
4. Polls `http://127.0.0.1:<port>/readyz` for up to
   `MYPORTAL_READY_TIMEOUT` seconds.
5. Re-enables the instance in the state file and reloads nginx.

If any step fails the script aborts with the still-drained instance
left out of rotation. The other instance keeps serving traffic; fix
the failed side manually and re-run `--rolling`.

### Multi-instance gotchas

* **Sessions must be shared.** Use the Redis-backed session store
  (already a project dependency) so a user mid-session is not pinned
  to one instance.
* **APScheduler must not double-fire.** Wrap any scheduled job that
  must run exactly once with `singleton_run` from
  `app/services/singleton_jobs.py`. The helper uses a database-backed
  lease so only one instance executes the job each tick.
* **In-process caches.** Audit `app/services/*` for caches that
  outlive a single request; move anything that must be consistent
  across instances into Redis.
* **Webhooks and external callbacks.** Both instances will receive
  callbacks via nginx; design idempotent handlers (already a
  project-wide rule for webhook retries).

## Expand/contract migration policy

Destructive schema changes are incompatible with both upgrade
strategies because the old and new code must coexist on the same
database for the duration of a deploy (a few seconds for graceful
reload, longer for rolling deploys).

When a destructive change is unavoidable, split it across **three**
releases:

1. **Expand release.** Add the new column/table and start
   double-writing. The old code continues to read and write the old
   column/table; it doesn't know the new one exists, and that's fine.
2. **Migrate release.** Move readers to the new column/table. The old
   column/table is still present, still being written by old code (if
   any still exists), and still readable by the new code as a
   fallback.
3. **Contract release.** Once you're certain no live instances depend
   on the old column/table, drop it.

A short `CONTRACT.md` note inside `migrations/` is encouraged for any
contract step so reviewers can confirm the prior expand/migrate
releases have shipped.
