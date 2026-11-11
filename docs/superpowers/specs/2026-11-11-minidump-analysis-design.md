# Windows blue screen (minidump) analysis — design

Date: 2026-11-11
Status: Approved design, awaiting spec review

## Goal

Give technicians a blue screen debugging tool on the edit asset page. A new
**Minidumps** section lists the Windows kernel minidumps found on the
computer; a technician clicks **Analyse** on one and the existing AI
troubleshooter returns the likely cause of the crash and how to fix it.

Success means: after a Windows computer with the tray app blue screens, its
asset page shows the crash (time, stop code, suspected driver) without anyone
asking for it, and one click produces a saved, plain-English diagnosis with
evidence, fix steps and cited articles, even while the computer is offline.

## Decisions

| Topic | Decision |
|---|---|
| Decoding | A parser built into the tray app reads the dump on the computer. Nothing is installed, no Microsoft debugger or symbols are used, and the `.dmp` file is never uploaded. |
| Discovery | The tray app finds and decodes dumps in the background and uploads a small summary of each. |
| When the AI runs | Only when a technician clicks **Analyse**. No automatic analysis. |
| Where analysis runs | Entirely on the server, from the stored summary, so it works when the computer is offline. LLM credentials never leave the server. |
| Result | Saved against the dump and shown on the asset page. The technician may also pick one of the asset's open tickets, and the result is then posted there as an internal note. |
| Research | Reuses the troubleshooter's knowledge base search and, when `TROUBLESHOOT_WEB_SEARCH_ENABLED` is on, its public web search. |
| Scope of dumps | Kernel minidumps in `%SystemRoot%\Minidump`, 64-bit (x64 and ARM64). |

## Existing building blocks

- `app/services/ai_troubleshooter.py` — `_chat` (LLM call with usage
  logging), `_search_kb`, `_extract_json_object`, `_string_list`, `_link`,
  `_note` and `_note_header` for internal ticket notes.
- `app/services/troubleshoot_web_search.py` — `is_enabled()` and
  `research(terms)`.
- `app/services/ai_prompt_security.py` — `UntrustedRecord` wrapping for
  evidence passed to the model.
- `app/services/ai_health_check.py` and `app/features/health_check/` — the
  closest existing feature: asset-scoped AI runs through the tray app, with
  `_research`, the `_rmm_access(request, asset_id=…, write=True)` permission
  check, tray-device bearer endpoints, AI consent and opt-out handling, and
  the `rmm_agent_deploy.tray_version_at_least` version gate.
- `app/templates/assets/detail.html` with `asset_edit_sections.js` — the
  `.ce-*` settings workspace the new section joins.
- `tray/internal/api/client.go` — authenticated device calls;
  `tray/service/main.go` — the service's background loops.

## Components

### 1. Tray app: `tray/internal/minidump` (Go)

**Parser** (`parser.go`, pure Go, builds on every platform so it is unit
tested on Linux):

- Input: a file path. Reads at most the first 8 MB.
- Accepts the 64-bit kernel dump header (signature `PAGE`, valid dump
  `DU64`). A 32-bit header (`DUMP`) returns a summary with
  `supported=false` and only what the 32-bit header offers reliably (stop
  code and parameters). Anything else is rejected as "not a kernel dump".
- From `DUMP_HEADER64`: bugcheck code and its four parameters, machine
  image type (x64 / ARM64), processor count, Windows major/minor build,
  crash time (`SystemTime`), uptime (`SystemUpTime`), dump type.
- From the triage block (`TRIAGE_DUMP64` at the offset the header gives):
  the loaded driver list (name, base address, size, link timestamp,
  checksum), the unloaded driver list (name, start, end), the raw call
  stack bytes, and the context and exception records.
- Derived evidence, computed on the device:
  - `faulting_address` (exception address, else the context instruction
    pointer) resolved to `driver+offset`.
  - each bugcheck parameter that falls inside a driver, as `driver+offset`.
  - `stack_drivers`: the stack scanned as 8-byte values; every value inside
    a loaded driver becomes `driver+offset`, in stack order, consecutive
    duplicates collapsed, capped at 64 entries.
  - `suspect_driver`: the first of faulting-address driver, parameter
    driver, stack driver that is not a Windows core image (`ntoskrnl.exe`,
    `ntkrnlmp.exe`, `hal.dll`, `win32k*.sys` and similar, kept in one
    list). Empty when only core images appear.
- Every offset and count read from the file is bounds-checked against the
  bytes actually read; counts are capped (512 drivers, 64 unloaded drivers,
  256 KB of stack). A dump that fails a check yields a summary with an
  `error` and whatever header fields were valid; the parser never panics.
- Driver names are reduced to their base file name and to printable
  characters, at most 64 characters.

**Scanner** (`scan_windows.go`, with a no-op for other platforms):

- Lists `%SystemRoot%\Minidump\*.dmp`, newest 20 by modified time, skipping
  files over 64 MB and anything that is not a regular file.
- Runs once two minutes after the service starts (a blue screen always ends
  in a restart) and then every hour.
- Keeps a cache file in the service's protected state folder keyed by file
  name, size and modified time, so a dump is decoded and uploaded once. The
  cache entry is written only after a successful upload.
- Uploads new summaries with `Client.PostMinidumps` to
  `POST /api/tray/minidumps`, at most 20 per request.

The tray version is bumped; no server-to-device command is added, so older
tray apps simply report nothing.

### 2. Database: migration `asset_minidumps`

Created with `/new-migration` (phase `expand`), idempotent.

| Column | Purpose |
|---|---|
| `id` | Primary key |
| `company_id`, `asset_id` | Scope; foreign keys with `ON DELETE CASCADE` |
| `tray_device_id` | Device that reported it |
| `file_name`, `file_size`, `file_modified_at` | Identity of the dump on the device |
| `crashed_at` | Crash time from the dump (UTC) |
| `bugcheck_code` (BIGINT), `bugcheck_name` | Stop code and its name |
| `suspect_driver` | Parser's suspect, for the list chip |
| `supported` | 0 for dumps the parser could not decode |
| `summary_json` | The decoded summary (validated and capped) |
| `analysis_status` | `none`, `analysing`, `completed`, `failed` |
| `analysis_json` | The model's report |
| `analysis_error`, `model` | Failure reason; model used |
| `ticket_id` | Ticket the last analysis was posted to, if any |
| `analysed_by_user_id`, `analysed_at` | Who and when |
| `created_at`, `updated_at` | Bookkeeping (UTC) |

Unique key on (`tray_device_id`, `file_name`, `file_size`,
`file_modified_at`) so a repeated upload updates nothing. Index on
(`asset_id`, `crashed_at`). The newest 50 dumps per asset are kept; ingest
deletes older rows.

`app/repositories/asset_minidumps.py` holds the queries, all parameterised
with no f-string SQL.

### 3. Server: `app/services/minidump_analysis.py`

**Ingest** — `ingest(device, dumps)`:

- Resolves the asset and company from the tray device; a device with no
  linked asset is accepted and ignored.
- Validates every field by type and range, rebuilds the summary from known
  keys only (unknown keys are dropped), caps list lengths and string
  lengths, and stores at most 64 KB of JSON per dump.
- Names the stop code from a built-in table of the common codes (about 80,
  for example `0x0000007E SYSTEM_THREAD_EXCEPTION_NOT_HANDLED`,
  `0x00000124 WHEA_UNCORRECTABLE_ERROR`); an unknown code is shown as its
  hex value.

**Analyse** — `start_analysis(dump, requested_by, ticket)` then a background
`run_analysis(dump_id)`:

1. Refuses when no troubleshooter LLM model is configured, when AI consent
   or opt-out rules forbid it for the company (same checks as the health
   check), when the dump is not `supported`, or when an analysis is already
   running. Sets `analysis_status='analysing'`.
2. Research terms are built by the server, not the model: the stop code
   name, and the suspect driver's file name when there is one (for example
   `DRIVER_IRQL_NOT_LESS_OR_EQUAL rtwlane.sys`). Knowledge base search is
   limited to public articles plus the asset's company. Web search runs
   only when enabled; a web failure is recorded in the result, not fatal.
3. One LLM call through `ai_troubleshooter._chat`, with the feature label
   "Minidump analysis" on the LLM Usage page. The system prompt carries the
   rules; the summary, the stop code's documented parameter meanings and
   the research text go in as `UntrustedRecord`s. The model must answer
   with one JSON object.
4. The report is parsed defensively into: `likely_cause` (one paragraph),
   `category` (`driver`, `hardware`, `memory`, `storage`, `firmware`,
   `windows`, `software`, `unknown`), `suspect` (driver or component),
   `confidence` (`high`, `medium`, `low`), `evidence` (list), `steps`
   (ordered list), `further_checks` (list) and `sources` (only URLs and
   article ids the server itself supplied are kept). Unparseable output is
   kept as plain text under `likely_cause` with `confidence=low`.
5. Saves the report with `analysis_status='completed'`. On any error saves
   `failed` with a short reason. Analyses stuck in `analysing` for more
   than 15 minutes are treated as failed when the list is next read.
6. When a ticket was chosen, posts the report as an internal note using the
   troubleshooter's note helpers. The ticket must belong to the asset's
   company and be linked to the asset; the note is skipped (and that is
   recorded on the result) if the ticket's requester has withdrawn AI
   consent.

The prompt states the limits of the evidence: no symbols, so the model must
name drivers rather than functions, must not invent stack frames, and must
say when the evidence only points at Windows core images.

### 4. Routes: `app/features/health_check/minidump_routes.py`

Added to the existing health check feature pack (version bumped), since it
shares that pack's access checks and device authentication.

| Route | Caller | Purpose |
|---|---|---|
| `POST /api/tray/minidumps` | Tray device bearer | Upload decoded summaries. Body capped at 2 MB; rate limited per device. |
| `GET /api/assets/{asset_id}/minidumps` | Staff, RMM read on the asset's company | List dumps with status and results (also used for live status). |
| `POST /api/assets/{asset_id}/minidumps/{dump_id}/analyse` | Staff, RMM write | Start an analysis; optional `ticket_id`. JSON. |
| `POST /rmm/assets/{asset_id}/minidumps/{dump_id}/analyse` | Staff, RMM write | Form version with CSRF; redirects to `/assets/{id}#minidumps` with a flash message. |

Every staff route checks the asset's company against the caller's access
with `_rmm_access`, and that the dump belongs to that asset; a mismatch is a
404. Starting an analysis writes a `minidump.analyse` audit entry. All
routes carry summaries and response models for Swagger.

### 5. Asset page: Minidumps section

- A `.ce-section` with id `minidumps` in the same nav group as *Health
  check*, with the dump count. Shown only when the asset is a Windows
  computer with a tray device and the viewer is staff with RMM access;
  never in the customer-safe view.
- Subtitle: what the section is for, in plain English.
- One card per dump (`.md-*` classes in `app.css`, shared tokens only):
  crash time (`data-utc`), stop code chip, suspected driver chip, uptime
  before the crash, and a status pill (*Not analysed*, *Analysing…*,
  *Analysed*, *Failed*).
- **Analyse** / **Analyse again** is a real form post with an optional
  *Also post to ticket* select listing the asset's open tickets. Disabled
  with an explanation when AI is not configured or not permitted.
- A completed analysis shows in the card inside `<details>`: likely cause,
  confidence and category chips, evidence list, numbered fix steps, further
  checks, sources as links, and who ran it and when. A collapsed *Decoded
  dump* block shows the raw summary (drivers on the stack, parameters) for
  technicians who want it.
- Unsupported dumps are listed with the reason and no Analyse button.
- Empty state: explains that blue screen dumps appear here automatically
  when the tray app finds them, and what to check if a crash is missing
  (minidumps turned off in Windows, tray app version).
- `app/static/js/asset_minidumps.js` polls the list endpoint while any dump
  is *Analysing* and updates the card through a `role="status"
  aria-live="polite"` region. Without JavaScript the page works by
  reloading.

### 6. Documentation and housekeeping

- `docs/wiki/administration/Minidump Analysis.md`, linked from the Health
  Check and Tray App pages; API notes in the tray API reference.
- A `changes/` entry via `/changelog-entry`.
- No new environment variables.

## Data flow

```
blue screen -> restart -> tray service scans %SystemRoot%\Minidump
  -> parser decodes new dumps -> POST /api/tray/minidumps
  -> asset_minidumps rows -> Minidumps section lists them
technician clicks Analyse (optional ticket)
  -> research (knowledge base, optional web) -> one LLM call
  -> report saved on the dump -> shown in the card (+ internal ticket note)
```

## Security and privacy

- The dump file stays on the computer. The summary holds stop code data,
  kernel addresses, driver file names and timestamps; no process memory,
  user names or file paths.
- Everything from the device is untrusted: validated on ingest, escaped in
  templates, and wrapped as untrusted evidence in the prompt.
- Company scoping on every query; device endpoints only write to the
  device's own asset.
- Only the generic stop code name and driver file name go to a web search
  provider.

## Error handling

| Situation | Behaviour |
|---|---|
| Minidump folder missing or unreadable | Scanner logs once and reports nothing |
| Corrupt or truncated dump | Listed as unsupported with the reason |
| Upload fails | Not cached; retried on the next scan |
| No LLM configured / AI not permitted | Analyse disabled with the reason shown |
| LLM error or timeout | `failed` with the reason; **Analyse again** available |
| Web search fails | Analysis continues; noted in the result |
| Ticket not valid for the asset | Request rejected with 400 |

## Testing

- **Go** (`tray/internal/minidump`): a test helper builds synthetic 64-bit
  dumps. Cases: header fields, driver list, address resolution, stack
  scan order and de-duplication, suspect choice skipping core images,
  32-bit header, truncated file, out-of-range offsets and oversized counts
  (no panic), and the scanner cache.
- **pytest**: ingest validation and capping, unknown keys dropped, repeated
  upload is idempotent, device without an asset, company scoping and
  permissions on each staff route, analyse refusals, report parsing
  (well-formed, malformed, sources filtered to supplied ones), ticket note
  posted only for a valid linked ticket, stale `analysing` recovery, and
  the asset page rendering the section and its empty state.

## Known limitation

No real blue screen dump is available in the development environment. The
parser is written from the published kernel dump layout and tested against
synthetic dumps, so it must be checked against a real dump from a customer
machine before the feature is relied on. If a sample `.dmp` is supplied, it
replaces the synthetic fixture for the header and driver-list tests (the
file itself is binary and is not committed; the test reads it from a path
given by an environment variable and is skipped without it).

## Out of scope

Full and kernel memory dumps (`MEMORY.DMP`), application crash dumps, uploading or
downloading the dump file, Microsoft symbols and function names, running a
debugger on the computer, automatic analysis of new dumps, automations or
notifications when a blue screen is reported, and macOS or Linux panics.
