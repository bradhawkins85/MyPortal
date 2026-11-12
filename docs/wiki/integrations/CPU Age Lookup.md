# CPU Age Lookup

The CPU age lookup module estimates how old each device is from the launch date
of its processor. A computer's real purchase date is rarely known, but it cannot
be older than its CPU, so the processor's launch date is a useful rough guide.

The estimate is stored in the asset's **Approx age** (whole years since the CPU
launched). It appears in the asset list's *Approx age* column, can be used in
device filters (*Approximate age (years)*), and the asset's **Inventory** section
shows the CPU launch date and where it came from.

## Where launch dates come from

Each processor model needs its launch date recorded once, on the
**CPU launch dates** page. Every device with that processor then gets its age.

| Processor | Source |
| --- | --- |
| Intel | [Intel ARK](https://www.intel.com/content/www/us/en/ark.html). The page links each Intel model to Intel's site; open its specification page and read **Launch Date** under *Supplemental Information* (for example `Q3'19` for the Core i7-1065G7). |
| AMD, Apple, Qualcomm and others | The maker's own specification page, or another reliable source. |
| Virtual CPUs (for example "Intel Core Processor (Broadwell)") | No model number. Record a date only if it matters. |

MyPortal does not fetch Intel ARK itself. Intel's old ARK search now redirects
to a 404 page, and intel.com refuses automated requests ("Access Denied"), so
the date is read in your own browser and typed in.

The processor name comes from the asset's synced CPU (Syncro or Tactical RMM),
or from the native RMM agent's hardware inventory when no synced CPU exists.
Different spellings of the same model (for example
`Intel(R) Core(TM) i7-1065G7 CPU @ 1.30GHz` and `Intel Core i7-1065G7`) are
grouped as one processor.

## CPU launch dates page

Super admins open **Assets → Actions → CPU launch dates** (`/assets/cpu-launch-dates`).
It lists every processor model your assets report, in two groups:

- **Needs a date** — models without a launch date yet, most-used first. Intel
  models have an **Open on Intel ARK** button that searches Intel's site for
  the model in a new tab.
- **Launch date known** — models with a date, where it came from (a link to the
  Intel page when one was recorded) and the resulting age.

Choose **Add date** or **Edit date** to open the editor:

- **Launch date** — type it as Intel ARK shows it (`Q3'19`). `Q3 2019`,
  `2019-07`, `07/2019`, `Jul 2019` and `2019` also work. A quarter counts from
  its first month, the earliest the processor could ship.
- **Where the date came from** — *Intel ARK* (the default for Intel
  processors) or *Other source*.
- **Intel ARK page** (optional) — paste the processor's intel.com page so
  others can check the date later. Only `https://` links on intel.com are
  accepted.

The editor previews the age the devices will get. Saving updates every device
with that processor straight away and is recorded in the audit log. **Clear
date** removes the date; devices keep their last age until a new date is
recorded.

## Setup

Turn on **CPU age lookup** in **Admin → Modules**. No credentials are needed.
Dates recorded on the page apply straight away even with the module off; the
module runs the daily refresh below.

## When it runs

- Daily at 05:45 (store time): registers processor models reported by new
  devices and recalculates every asset's age so it keeps counting up.
- On demand: add a scheduled task with the command **Estimate asset ages from
  CPU launch dates**, or call `POST /api/cpu-release-dates/refresh`.

Once an asset has a CPU-based age, Syncro and Tactical RMM syncs no longer
overwrite its *Approx age*.

## API (super admin)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/cpu-release-dates` | List processor models and their launch dates. |
| `POST` | `/api/cpu-release-dates/refresh` | Register new processor models and recalculate ages now. |
| `PUT` | `/api/cpu-release-dates/{id}` | Record a launch date (`{"launch_date": "Q3'19", "source": "intel_ark", "source_url": "https://www.intel.com/…"}`), or send `"launch_date": null` to clear it. |

Changes are recorded in the audit log.
