# CPU Age Lookup

The CPU age lookup module estimates how old each device is from the launch date
of its processor. A computer's real purchase date is rarely known, but it cannot
be older than its CPU, so the processor's launch date is a useful rough guide.

The estimate is stored in the asset's **Approx age** (whole years since the CPU
launched). It appears in the asset list's *Approx age* column, can be used in
device filters (*Approximate age (years)*), and the asset's **Inventory** section
shows the CPU launch date and where it came from.

## Where launch dates come from

| Processor | Source |
| --- | --- |
| Intel | Always [Intel ARK](https://ark.intel.com), Intel's own processor specification database. Only the model number (for example `i7-8650U`) is sent. |
| AMD, Apple, Qualcomm and others | No free automated source. Record the launch date by hand once per model; it then applies to every device with that processor. |
| Virtual CPUs (for example "Intel Core Processor (Broadwell)") | No model number, so no lookup. Record the date by hand if it matters. |

Each distinct processor model is looked up once and remembered, so a fleet of
identical laptops costs one request. Quarters reported by ARK (for example
`Q3'17`) are stored as the first day of that quarter.

The processor name comes from the asset's synced CPU (Syncro or Tactical RMM),
or from the native RMM agent's hardware inventory when no synced CPU exists.

## Setup

Turn on **CPU age lookup** in **Admin → Modules**. No credentials are needed.

Optional tuning in `.env`:

```
CPU_RELEASE_LOOKUP_RETRY_DAYS=30   # retry processors not found after this many days
CPU_RELEASE_LOOKUP_BATCH_SIZE=25   # processor models looked up per run
```

## When it runs

- Daily at 05:45 (store time): finds new processor models, looks up the ones
  without a launch date, and recalculates every asset's age so it keeps
  counting up.
- On demand: add a scheduled task with the command **Estimate asset ages from
  CPU launch dates**, or call `POST /api/cpu-release-dates/refresh`.

Once an asset has a CPU-based age, Syncro and Tactical RMM syncs no longer
overwrite its *Approx age*.

## API (super admin)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/cpu-release-dates` | List processor models, their launch dates and lookup status. |
| `POST` | `/api/cpu-release-dates/refresh` | Run a lookup now (in the background). |
| `PUT` | `/api/cpu-release-dates/{id}` | Record a launch date by hand (`{"release_date": "2021-01-01"}`), or send `null` to clear it so the next run looks it up again. |

Manual changes are recorded in the audit log.
