# Minidump Analysis (Windows blue screens)

The **Minidumps** section on an asset page (`/assets/{id}#minidumps`) lists the
blue screen crashes found on a Windows computer and lets a technician have the
AI troubleshooter explain the likely cause of one.

## What you see

Each crash is a card showing:

- when the computer crashed and the dump's file name
- the stop code and its name, for example
  `0x000000D1 DRIVER_IRQL_NOT_LESS_OR_EQUAL`
- the driver the dump points at (**Suspected driver**), or *No third-party
  driver identified* when only Windows' own components appear
- how long the computer had been up before the crash
- whether it has been analysed

**Decoded dump** at the bottom of a card shows the raw details for technicians
who want them: the stop code's four parameters and what they mean, the faulting
address, and the drivers found on the crash stack.

## How a crash gets here

The tray app does this by itself; nobody has to ask for it.

1. Two minutes after its service starts (a blue screen always ends in a
   restart), and then every hour, the tray app looks in `C:\Windows\Minidump`.
2. It decodes each new dump on the computer and sends MyPortal a small summary:
   the stop code and its parameters, the crash time, the drivers that were
   loaded and the drivers on the crash stack.
3. The crash appears on the computer's asset page.

**The dump file never leaves the computer.** Only the summary is sent. It holds
driver file names, addresses and times; it holds no user names, file paths or
memory contents.

The tray app reports the 20 newest dumps and MyPortal keeps the 50 newest per
asset. Because the summary is stored in MyPortal, a crash can be analysed while
the computer is offline.

Windows must be set to write small memory dumps: *System Properties → Advanced →
Startup and Recovery → Write debugging information → Small memory dump (256 KB)*
or *Automatic memory dump*. The dumps are written to `%SystemRoot%\Minidump`.

## Analysing a crash

Click **Analyse** on a crash. Optionally pick one of the asset's open tickets
under **Also post to ticket**; the result is then also added to that ticket as
an internal note.

The AI troubleshooter then:

1. Searches the knowledge base (public articles and the asset's company) for
   the stop code and the suspected driver. When
   `TROUBLESHOOT_WEB_SEARCH_ENABLED` is on it also searches the public web.
   Only the stop code's name and the driver's file name are sent to the search
   provider.
2. Sends the decoded summary and what it found to the troubleshooter's AI model
   (the same model as the ticket troubleshooter and the health check).
3. Saves the result on the crash: the likely cause, how confident it is, the
   evidence, the steps to fix it, further checks and the articles it used.

The page updates when the analysis finishes, usually within a minute. Use
**Analyse again** to repeat it, for example after adding a knowledge base
article. Nothing is analysed automatically, so no AI usage is spent until a
technician asks. Each run is listed as **Minidump analysis** on the LLM Usage
page.

### Reading the result

The dump has no debugging symbols, so the analysis names **drivers**, never
functions, and "drivers on the crash stack" are leads rather than a verified
call stack. Treat the result as a strong starting point to confirm, not a
verdict:

- **High confidence** usually means the stop code, its parameters and the stack
  all point at the same third-party driver.
- **Low confidence** means the evidence does not single out a cause. This is
  common for hardware faults (`WHEA_UNCORRECTABLE_ERROR`), where the dump only
  shows Windows' own components.

## Who can use it

The section is shown to staff with **RMM scripts** access to the asset's
company, on Windows computers with the tray app. It needs the RMM and Health
check feature packs, but not the RMM agent. Analysing needs RMM scripts *write*
access. It is never shown in the customer view.

An analysis is refused when no AI model is configured for the AI troubleshooter.
A result is not posted to a ticket whose requester has opted out of AI
processing; the analysis itself is still saved on the asset.

## Limits

- 64-bit kernel minidumps only (x64 and ARM64). A 32-bit dump is listed as
  *Not decoded*.
- Full and kernel memory dumps (`MEMORY.DMP`) and application crash dumps are
  not read.
- Needs a tray app version that reports dumps; older versions report nothing
  until they update.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| No crashes listed | Windows is not writing small memory dumps, the tray app is out of date, or the tray device is not linked to this asset. Crashes appear up to an hour after the tray app starts. |
| *Not decoded* | The file is a 32-bit dump, is not a kernel minidump, or was damaged when Windows wrote it. |
| *Failed* | The AI model did not answer or returned nothing usable; the reason is shown on the card. Click **Analyse again**. An analysis that has not finished after 15 minutes is marked failed. |
| A wrong crash time | The time comes from the dump. When it is missing or implausible, the dump file's own time is used. |

## API

All routes appear in the Swagger UI at `/docs`.

| Route | Method | Caller | Purpose |
| --- | --- | --- | --- |
| `/api/tray/minidumps` | POST | Device bearer | Report decoded dumps: `{"dumps": [...]}`, at most 20 per request and 2 MB; six reports per device in ten minutes |
| `/api/assets/{asset_id}/minidumps` | GET | RMM scripts access | The asset's crashes and their analyses |
| `/api/assets/{asset_id}/minidumps/{dump_id}/analyse` | POST | RMM scripts write access | Start an analysis; body `{"ticket_id": int|null}`. `202` when started, `409` when one is already running, `400` when the ticket is not linked to the asset |
| `/rmm/assets/{asset_id}/minidumps/{dump_id}/analyse` | POST | RMM scripts write access | The same from the asset page's form |

Starting an analysis is recorded in the audit log as `minidump.analyse`.

Disable the feature with `DISABLED_FEATURE_PACKS=health_check`; reported crashes
stay in the database.

## For developers

- Tray app: `tray/internal/minidump` (parser and scanner) and
  `tray/service/minidumps.go` (the hourly loop).
- Server: `app/services/minidump_analysis.py`,
  `app/services/minidump_stop_codes.py`, `app/repositories/asset_minidumps.py`
  and `app/features/health_check/minidump_routes.py`.
- The parser is tested against synthetic dumps. To check it against a real one:

  ```sh
  cd tray
  MYPORTAL_TEST_MINIDUMP=/path/to/a.dmp go test -run TestRealDump -v ./internal/minidump/
  ```
