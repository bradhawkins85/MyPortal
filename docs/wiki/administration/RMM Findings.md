# RMM Findings

Findings are a best-guess look across **every company's** RMM data for things worth a technician's
time: problems starting on several devices, security concerns and patterns no single device page
shows. Super admins open them from **Findings** in the main menu (`/rmm/findings`).

## What an analysis looks at

Every `FINDINGS_INTERVAL_HOURS` (6 by default) MyPortal compares the last 24 hours with the
`FINDINGS_BASELINE_DAYS` (7 by default) before them. A super admin can also start one with
**Run analysis**.

| Area | What is found | Data |
|------|---------------|------|
| **Event logs** | Event types far more frequent than usual (at least 20 entries and three times the daily average), and new event types on three or more devices. | [Event log database](RMM%20Event%20Logs.md) |
| **Security** | Accounts locked out three or more times and the caller computers causing it (matched to managed devices by name); similar new user accounts (`jsmith`, `j.smith`, `jsmith2`) on three or more devices or in two or more companies; new services, especially ones running from temporary, user or script locations; members added to admin groups on several devices; cleared event logs. | Security and System logs (event IDs 4740, 4720, 4728, 4732, 4756, 1102, 104 and 7045) |
| **Software changes** | Programs installed, removed or updated since the previous analysis; security software removed; remote access tools installed; event spikes that began on the devices where a program was installed or updated. | [Inventory](RMM%20Inventory.md) |
| **Performance** | Devices whose average CPU or memory use is well above devices with the same processor, memory and operating system (at least three such devices). | [Device stats](RMM%20Checks.md) |
| **Device health** | System drives with under 5 GB free, devices not restarted in 30 days, agents silent for 14 days, and checks failing on a quarter or more of the devices they cover. | Inventory and checks |

Security events are only found when the Security log is collected: add it for every device with
`EVENT_LOG_DEFAULT_LOGS=Application;System;Security`, or for a company or device on the
**Event logs** page. Domain controllers' Security logs are where lockouts are recorded.

Inventory keeps only each device's latest software, so the first analysis records what every
device has and counts only programs whose install date falls in the look-back window. Later
analyses compare with the previous one.

## AI insight

With `FINDINGS_AI_ENABLED=true` the signals above are summarised and sent to the language model in
`FINDINGS_LLM_BASE_URL` / `FINDINGS_LLM_MODEL` / `FINDINGS_LLM_API_KEY` (any OpenAI-compatible
`/v1/chat/completions` endpoint, including Ollama). With none of them set the
AI troubleshooter's `TROUBLESHOOT_LLM_*` settings are used.
The model is asked for findings the rules cannot see, such as links between signals, with a
confidence score.

What is sent: device and company names, event counts with a 240-character excerpt of one sample
message per event type, account, service and program names, and spec and usage figures. Full event
logs are never sent. The reply is checked before it is stored: only devices and companies that were
in the data are kept, and malformed items are dropped. Requests appear under **RMM findings** in
LLM usage.

If the model fails, the analysis still finishes: rule findings are updated and AI findings from
earlier analyses stay until the model answers again.

## Reviewing findings

Choose a finding to see what was found, the evidence, suggested next steps and the affected devices
and companies. Then:

* **Acknowledge** when someone is on it.
* **Dismiss** when it is expected. A dismissed finding stays dismissed if found again.
* **Reopen** to return it to the review list.

Add an optional note to say what was done. Review changes are audit logged.

The same finding found again updates its card (**Found in N analyses**) instead of adding another.
Findings an analysis no longer sees are marked **No longer detected** and kept for 90 days; choose
**Include no longer detected** to see them. A source that could not be read (for example the event
log database being down) keeps its findings until it can be read again.

## Settings

| Variable | Default | Effect |
|----------|---------|--------|
| `FINDINGS_ENABLED` | `true` | Scheduled analyses. Off still allows **Run analysis**. |
| `FINDINGS_AI_ENABLED` | `true` | Send the signals to the language model. |
| `FINDINGS_INTERVAL_HOURS` | `6` | Hours between scheduled analyses (1-168). |
| `FINDINGS_BASELINE_DAYS` | `7` | Days the last 24 hours are compared with (2-30). |
| `FINDINGS_LLM_BASE_URL`, `_MODEL`, `_API_KEY` | empty | The findings model; empty uses `TROUBLESHOOT_LLM_*`. |

## API

Super admins only:

* `GET /api/rmm/findings?include_inactive=true|false`
* `GET /api/rmm/findings/runs/latest`
* `POST /api/rmm/findings/runs` start an analysis (409 while one is running)
* `PATCH /api/rmm/findings/{id}` with `{"status": "open|acknowledged|dismissed", "note": "…"}`
