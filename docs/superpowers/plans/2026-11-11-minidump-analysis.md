# Windows Minidump Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A *Minidumps* section on the edit asset page that lists a Windows computer's blue screen dumps and lets a technician have the AI troubleshooter explain the likely cause of one.

**Architecture:** The tray app (Go) decodes kernel minidumps on the computer and uploads a small JSON summary of each. MyPortal stores the summaries in `asset_minidumps`; an analysis runs entirely on the server (research, one LLM call) from the stored summary and saves a structured report on the row, optionally posting it to a ticket.

**Tech Stack:** Go (tray module, standard library only), Python 3.11 / FastAPI / async DB layer (`app.core.database.db`), Jinja2, vanilla JS, pytest + aiosqlite.

**Spec:** `docs/superpowers/specs/2026-11-11-minidump-analysis-design.md` — read it first; this plan does not repeat its reasoning.

## Global Constraints

- Project rules in `.github/agents.md` apply to every task (parameterised SQL with no f-strings, type hints, design guidelines, no binary files, UTC in the database, `data-utc` for displayed times).
- The `.dmp` file is never uploaded; only the decoded summary leaves the computer.
- Dumps decoded: 64-bit kernel minidumps (x64 `0x8664`, ARM64 `0xAA64`) in `%SystemRoot%\Minidump`. A 32-bit dump is listed with `supported=false`.
- Tray limits: newest 20 `.dmp` files, skip files over 64 MB, read at most the first 8 MB, 512 drivers, 64 unloaded drivers, 256 KB of stack, 64 stack-driver entries, driver names 64 printable characters.
- Server limits: upload body 2 MB, 20 dumps per request, 64 KB of summary JSON per dump, newest 50 dumps kept per asset, an analysis stuck for 15 minutes counts as failed.
- The AI runs only when a technician clicks **Analyse**. LLM credentials never leave the server.
- No new environment variables. No new server-to-device command. The tray version comes from the release tag (`updater.AgentVersion` is set by ldflags), so there is no version file to edit and no minimum-version gate: older tray apps simply report nothing.
- New migration via the `/new-migration` skill; change log entry via `/changelog-entry`.
- Go tests: `cd tray && go test -count=1 ./internal/minidump/ ./service/`. Python tests: `pytest tests/test_minidump_analysis.py -q`.

## Review Focus

Each line is pinned by a test in the task named.

1. **A dump still being written when the scan runs** — it must not be recorded as corrupt forever. The scanner skips files modified in the last 2 minutes (Task 2).
2. **A wrong device clock or an empty crash time in the dump** (FILETIME 0, or a date years away) — the list must still sort and display sensibly. Ingest drops a `crashed_at` before 2000-01-01 or more than 1 day ahead and falls back to `file_modified_at`, then to the upload time (Task 4).
3. **A tray device not linked to its asset by `asset_id`** (matched only by `assets.tray_device_uid` or serial number, as the health check resolves it) — its dumps must still land on the asset (Task 3).
4. **Two technicians clicking Analyse at the same moment, or a double-click** — one analysis runs; the second gets "already running" (Task 5).
5. **Model output or driver names containing HTML** — shown as text on the asset page and in the ticket note, never rendered (Tasks 5 and 7).

---

## File Structure

| File | Responsibility |
|---|---|
| `tray/internal/minidump/layout.go` | Every dump offset and limit as a named constant, with its source |
| `tray/internal/minidump/parser.go` | `Parse` — bytes in, `Summary` out |
| `tray/internal/minidump/testdump_test.go` | Builder for synthetic 64-bit dumps |
| `tray/internal/minidump/parser_test.go` | Parser tests |
| `tray/internal/minidump/scan.go` | Folder listing, cache, `Scan` |
| `tray/internal/minidump/scan_test.go` | Scanner and cache tests |
| `tray/internal/api/client.go` | `PostMinidumps` |
| `tray/service/minidumps.go` | The service's scan loop |
| `migrations/<n>_asset_minidumps.sql` | Table |
| `app/repositories/asset_minidumps.py` | Queries |
| `app/services/minidump_stop_codes.py` | Stop code names and parameter meanings |
| `app/services/minidump_analysis.py` | Ingest, analysis, presentation |
| `app/services/llm_usage.py` | Register the "Minidump analysis" feature |
| `app/features/health_check/minidump_routes.py` | Routes and the asset page context |
| `app/features/health_check/__init__.py` | Register the router, bump the pack version |
| `app/features/assets/routes.py` | Add `minidumps` to the asset page context |
| `app/templates/assets/detail.html`, `app/templates/assets/_minidumps.html` | Nav entry and section |
| `app/static/css/app.css`, `app/static/js/asset_minidumps.js` | `.md-*` styles, live status |
| `tests/test_minidump_analysis.py` | All Python tests |
| `docs/wiki/administration/Minidump Analysis.md` and links | Documentation |

## The summary contract (shared by Tasks 1–7)

The tray app uploads `{"dumps": [Summary, …]}`. 64-bit values are hex strings because JSON numbers cannot hold them.

```json
{
  "file_name": "111126-8765-01.dmp",
  "file_size": 1482304,
  "file_modified_at": "2026-11-11T02:03:04Z",
  "supported": true,
  "error": "",
  "architecture": "x64",
  "bugcheck_code": 209,
  "bugcheck_parameters": ["0x0000000000000028", "0x0000000000000002", "0x0000000000000000", "0xfffff80512345678"],
  "crashed_at": "2026-11-11T02:01:00Z",
  "uptime_seconds": 86400,
  "windows_build": 22631,
  "processor_count": 8,
  "dump_type": 4,
  "faulting_address": {"address": "0xfffff80512345678", "driver": "rtwlane.sys", "offset": "0x5678"},
  "parameter_drivers": [{"parameter": 4, "address": "0xfffff80512345678", "driver": "rtwlane.sys", "offset": "0x5678"}],
  "stack_drivers": [{"driver": "rtwlane.sys", "offset": "0x5678"}, {"driver": "ntoskrnl.exe", "offset": "0x3f5a10"}],
  "suspect_driver": "rtwlane.sys",
  "drivers": [{"name": "rtwlane.sys", "base": "0xfffff80512340000", "size": 9437184, "timestamp": "2021-03-04T05:06:07Z", "checksum": 123456}],
  "unloaded_drivers": [{"name": "dump_storahci.sys", "start": "0xfffff80600000000", "end": "0xfffff80600020000"}]
}
```

`architecture` is `x64`, `arm64`, `x86` or `unknown`. `faulting_address` is `null` when it resolves to no driver address. `timestamp` is `""` when the link timestamp is 0. `crashed_at` is `""` when the FILETIME is 0.

---

### Task 1: Dump parser (Go)

**Files:**
- Create: `tray/internal/minidump/layout.go`, `tray/internal/minidump/parser.go`
- Test: `tray/internal/minidump/testdump_test.go`, `tray/internal/minidump/parser_test.go`

**Interfaces:**
- Produces:
  - `type Summary struct` with the JSON fields of the summary contract (file fields are left zero by the parser), plus `type AddressRef struct{ Address, Driver, Offset string }`, `ParameterRef` (`Parameter int` + the same), `StackRef{ Driver, Offset string }`, `Driver{ Name, Base string; Size uint32; Timestamp string; Checksum uint32 }`, `UnloadedDriver{ Name, Start, End string }`.
  - `func Parse(data []byte) Summary` — never panics, never returns an error; failures go in `Summary.Error` with `Supported=false`.
  - `func IsCoreImage(name string) bool`.

- [ ] **Step 1: Cross-check the layout before writing it.** The offsets below are from the published definitions (`DUMP_HEADER64`, `DUMP_HEADER32`, `TRIAGE_DUMP64`, `DUMP_DRIVER_ENTRY64`, `DUMP_UNLOADED_DRIVERS64`, and `LDR_DATA_TABLE_ENTRY64` in `wdbgexts.h`). Compare each against two independent public sources (for example the ReactOS headers and the `kdmp-parser` structs) and record the sources in a comment at the top of `layout.go`. If a source disagrees with a value here, stop and report it rather than guessing.

  All integers little-endian.

  | Structure | Field | Offset | Type |
  |---|---|---|---|
  | header (both) | Signature `PAGE` | 0x000 | 4 bytes |
  | header (both) | ValidDump: `DU64` = 64-bit, `DUMP` = 32-bit | 0x004 | 4 bytes |
  | header (both) | MajorVersion | 0x008 | u32 |
  | header (both) | MinorVersion (the Windows build) | 0x00C | u32 |
  | `DUMP_HEADER64` | MachineImageType | 0x030 | u32 |
  | | NumberProcessors | 0x034 | u32 |
  | | BugCheckCode | 0x038 | u32 |
  | | BugCheckParameter1–4 | 0x040, 0x048, 0x050, 0x058 | u64 |
  | | ContextRecord | 0x348 | 3000 bytes |
  | | Exception (`EXCEPTION_RECORD64`); ExceptionAddress at +0x10 | 0xF00 | u64 |
  | | DumpType | 0xF98 | u32 |
  | | SystemTime (FILETIME) | 0xFA8 | u64 |
  | | SystemUpTime (100 ns units) | 0x1030 | u64 |
  | | header size; the triage block follows | 0x2000 | |
  | `DUMP_HEADER32` | MachineImageType, NumberProcessors, BugCheckCode | 0x020, 0x024, 0x028 | u32 |
  | | BugCheckParameter1–4 | 0x02C, 0x030, 0x034, 0x038 | u32 |
  | `TRIAGE_DUMP64` (at 0x2000) | UnloadedDriversOffset | +0x18 | u32 |
  | | CallStackOffset, SizeOfCallStack | +0x28, +0x2C | u32 |
  | | DriverListOffset, DriverCount | +0x30, +0x34 | u32 |
  | | StringPoolOffset, StringPoolSize | +0x38, +0x3C | u32 |
  | `DUMP_DRIVER_ENTRY64` (0x90 bytes each) | DriverNameOffset (file offset of a `DUMP_STRING`) | +0x00 | u32 |
  | | DllBase | +0x38 | u64 |
  | | SizeOfImage | +0x48 | u32 |
  | | CheckSum | +0x80 | u32 |
  | | TimeDateStamp (Unix seconds) | +0x88 | u32 |
  | `DUMP_STRING` | Length in UTF-16 characters, then the characters | +0x00, +0x04 | u32, UTF-16LE |
  | unloaded list (at UnloadedDriversOffset) | count, then 56-byte entries | +0x00, +0x08 | u64 |
  | unloaded entry | DriverName (12 UTF-16 chars, NUL padded) | +0x10 | 24 bytes |
  | | StartAddress, EndAddress | +0x28, +0x30 | u64 |
  | CONTEXT | instruction pointer: x64 `Rip` at +0xF8, ARM64 `Pc` at +0x108 | | u64 |

  All offsets in the triage block are file offsets from the start of the dump.

- [ ] **Step 2: Write the test dump builder** in `testdump_test.go`: `type testDump struct` with fields for every value in the table and `func (d testDump) bytes() []byte` that lays out a header, a triage block, a string pool, a driver list, an unloaded list and a stack. `func baseDump() testDump` returns: x64, 8 processors, build 22631, bugcheck `0xD1` with parameters `0x28, 2, 0, 0xfffff80512345678`, SystemTime for `2026-11-11T02:01:00Z`, uptime 86400 s, dump type 4, drivers `ntoskrnl.exe` (base `0xfffff80500000000`, size `0x1000000`) and `rtwlane.sys` (base `0xfffff80512340000`, size `0x900000`, timestamp `2021-03-04T05:06:07Z`), exception address `0xfffff80512345678`, stack values `[0x1, 0xfffff80512345678, 0xfffff80512345678, 0xfffff805003f5a10]`, one unloaded driver `dump_storahci.sys`.

- [ ] **Step 3: Write the failing tests** in `parser_test.go`:

```go
func TestParseHeaderAndDrivers(t *testing.T) {
	s := Parse(baseDump().bytes())
	if !s.Supported || s.Error != "" { t.Fatalf("supported=%v error=%q", s.Supported, s.Error) }
	if s.Architecture != "x64" || s.BugcheckCode != 0xD1 || s.WindowsBuild != 22631 || s.ProcessorCount != 8 { t.Fatalf("%+v", s) }
	if s.BugcheckParameters[3] != "0xfffff80512345678" { t.Fatal(s.BugcheckParameters) }
	if s.CrashedAt != "2026-11-11T02:01:00Z" || s.UptimeSeconds != 86400 { t.Fatal(s.CrashedAt, s.UptimeSeconds) }
	if len(s.Drivers) != 2 || s.Drivers[1].Name != "rtwlane.sys" || s.Drivers[1].Timestamp != "2021-03-04T05:06:07Z" { t.Fatalf("%+v", s.Drivers) }
	if len(s.UnloadedDrivers) != 1 || s.UnloadedDrivers[0].Name != "dump_storahci.sys" { t.Fatalf("%+v", s.UnloadedDrivers) }
}

func TestParseResolvesAddressesAndSuspect(t *testing.T) {
	s := Parse(baseDump().bytes())
	if s.FaultingAddress == nil || s.FaultingAddress.Driver != "rtwlane.sys" || s.FaultingAddress.Offset != "0x5678" { t.Fatalf("%+v", s.FaultingAddress) }
	if len(s.ParameterDrivers) != 1 || s.ParameterDrivers[0].Parameter != 4 { t.Fatalf("%+v", s.ParameterDrivers) }
	want := []StackRef{{"rtwlane.sys", "0x5678"}, {"ntoskrnl.exe", "0x3f5a10"}} // consecutive duplicate collapsed
	if !reflect.DeepEqual(s.StackDrivers, want) { t.Fatalf("%+v", s.StackDrivers) }
	if s.SuspectDriver != "rtwlane.sys" { t.Fatal(s.SuspectDriver) }
}
```

  Also, each as its own test with the assertion stated:
  - `TestSuspectEmptyWhenOnlyCoreImages` — exception address, parameters and stack all inside `ntoskrnl.exe` → `SuspectDriver == ""`, `Supported == true`.
  - `TestSuspectFallsBackToStack` — exception address `0`, no parameter in a driver → suspect is the first non-core stack driver.
  - `TestExceptionAddressZeroUsesContextIP` — exception address `0`, context `Rip` inside `rtwlane.sys` → `FaultingAddress.Driver == "rtwlane.sys"`.
  - `TestArm64UsesPc` — machine `0xAA64`, `Pc` at context +0x108 → `Architecture == "arm64"` and the faulting driver resolves.
  - `Test32BitHeader` — `PAGEDUMP` header with bugcheck `0x7E` → `Supported == false`, `Architecture == "x86"`, `BugcheckCode == 0x7E`, `Error == "32-bit dumps are not supported"`.
  - `TestNotADump` — `[]byte("MDMP…")` and an empty slice → `Supported == false`, `Error == "not a kernel dump"`.
  - `TestTruncatedDumpKeepsHeader` — `baseDump().bytes()[:0x2000]` → `Supported == false`, `BugcheckCode == 0xD1`, `Error != ""`.
  - `TestHostileOffsetsDoNotPanic` — table test: DriverListOffset past the end, DriverCount `0xFFFFFFFF`, CallStackOffset past the end, SizeOfCallStack `0xFFFFFFFF`, a DriverNameOffset past the end, a `DUMP_STRING` length of `0xFFFFFFFF`, unloaded count `1<<40` → no panic; `len(s.Drivers) <= 512`, `len(s.UnloadedDrivers) <= 64`, `len(s.StackDrivers) <= 64`.
  - `TestDriverNameIsSanitised` — a name `C:\Windows\System32\drivers\<script>évil.sys` → `"scriptvil.sys"` (base name, printable ASCII letters, digits, `.`, `_`, `-` only, at most 64 characters).
  - `TestZeroTimesAreEmpty` — SystemTime `0` and driver timestamp `0` → `CrashedAt == ""`, `Timestamp == ""`.
  - `FuzzParse` — seeds: the base dump; property: `Parse` returns without panic.

- [ ] **Step 4: Run** `cd tray && go test -count=1 ./internal/minidump/` — Expected: FAIL (undefined: Parse).

- [ ] **Step 5: Implement `layout.go` and `parser.go`.**
  - Read every field through one bounds-checked helper (`u32(data, off) (uint32, bool)` and the like); a failed read on the header returns the "not a kernel dump" or truncated error, a failed read in the triage block sets `Error` and stops reading that list but keeps what was read.
  - Core images (`IsCoreImage`, case-insensitive): `ntoskrnl.exe`, `ntkrnlmp.exe`, `ntkrnlpa.exe`, `ntkrpamp.exe`, `hal.dll`, `halmacpi.dll`, `kdcom.dll`, `ci.dll`, `clfs.sys`, `win32k.sys`, `win32kbase.sys`, `win32kfull.sys`, `fltmgr.sys`, `ndis.sys`, `tcpip.sys`, `wdf01000.sys`, `acpi.sys`, `ntfs.sys`, `volsnap.sys`, `storport.sys`, `dxgkrnl.sys`, `dxgmms1.sys`, `dxgmms2.sys`.
  - Resolve an address by finding the driver with `base <= addr < base+size`.
  - Stack scan: step 8 bytes; a value inside a driver becomes a `StackRef`; skip one equal to the previous entry; stop at 64 entries.
  - Suspect: first non-core among the faulting-address driver, then parameter drivers in order, then stack drivers in order.
  - Hex strings are `0x` + 16 lower-case digits for addresses and `0x` + minimal lower-case digits for offsets.

- [ ] **Step 6: Run** `cd tray && go test -count=1 ./internal/minidump/ && go test -run=^$ -fuzz=FuzzParse -fuzztime=30s ./internal/minidump/ && go vet ./internal/minidump/` — Expected: PASS, no fuzz failures.

- [ ] **Step 7: Add the optional real-dump test** `TestRealDump` in `parser_test.go`: skipped unless `MYPORTAL_TEST_MINIDUMP` names a file; asserts `Supported`, a non-zero `BugcheckCode`, at least 20 drivers, one of them a core image, and a non-empty `CrashedAt`. Run the package again; Expected: PASS with `TestRealDump` skipped.

- [ ] **Step 8: Commit** `git add tray/internal/minidump && git commit -m "Add a kernel minidump parser to the tray app"`

---

### Task 2: Scanner, upload and service loop (Go)

**Files:**
- Create: `tray/internal/minidump/scan.go`, `tray/internal/minidump/scan_test.go`, `tray/service/minidumps.go`, `tray/service/minidumps_test.go`
- Modify: `tray/internal/api/client.go` (after `PostHealthCheckResult`), `tray/service/main.go` (start the loop next to `rmmVersionLoop`)

**Interfaces:**
- Consumes: `minidump.Parse`, `minidump.Summary` (Task 1); `stateDir()` in `tray/service/main.go`.
- Produces:
  - `func Scan(dir, cachePath string, now time.Time) (pending []Summary, commit func() error, err error)` — summaries not yet uploaded, with `FileName`, `FileSize`, `FileModifiedAt` filled; `commit` records them in the cache and is called only after a successful upload.
  - `func Dir() string` — `%SystemRoot%\Minidump` on Windows, `""` elsewhere (build-tagged files `dir_windows.go` / `dir_other.go`).
  - `func (c *Client) PostMinidumps(ctx context.Context, dumps []minidump.Summary) error` — `POST {baseURL}/api/tray/minidumps`, JSON body `{"dumps": […]}`, device bearer auth; any status other than 2xx is an error.

- [ ] **Step 1: Write the failing scanner tests** in `scan_test.go`, using `t.TempDir()` and dumps from `baseDump().bytes()`:
  - `TestScanReturnsNewDumpsOnce` — two `.dmp` files → 2 pending; after `commit()`, a second `Scan` returns 0; touching one file's modified time returns that 1 again.
  - `TestScanWithoutCommitRetries` — `Scan` twice without `commit` → 2 pending both times.
  - `TestScanSkipsRecentlyWrittenFiles` — a file with modified time `now - 30s` is not returned; with `now - 3m` it is. *(Review Focus 1)*
  - `TestScanLimits` — 25 files → the 20 newest by modified time; a 65 MB sparse file (`os.Truncate`) is skipped; a sub-folder named `x.dmp` and a `notes.txt` are skipped.
  - `TestScanMissingFolder` — a folder that does not exist → `nil, no-op commit, nil`.
  - `TestScanCorruptCache` — a cache file containing `not json` → treated as empty, no error.
  - `TestScanCachePrunesDeletedFiles` — after `commit`, delete a dump, scan and commit again → the cache file no longer names it.

- [ ] **Step 2: Run** `cd tray && go test -count=1 ./internal/minidump/` — Expected: FAIL (undefined: Scan).

- [ ] **Step 3: Implement `scan.go`.** Cache is JSON `{"<file name>": {"size": n, "modified": "<RFC3339Nano>"}}` written with mode `0600` via a temp file and rename. Read each dump with `io.LimitReader` at 8 MB. Use `os.Lstat` and require `Mode().IsRegular()`.

- [ ] **Step 4: Write the failing client test** in `tray/internal/api/minidumps_test.go` with `httptest.NewServer`: asserts the path `/api/tray/minidumps`, the `Authorization: Bearer` header, a body that decodes to `{"dumps": [{"file_name": "a.dmp", …}]}`, and that a 500 response returns an error. Then implement `PostMinidumps` in the style of `PostHealthCheckStatus`.

- [ ] **Step 5: Write the failing service test** in `tray/service/minidumps_test.go` for `func runMinidumpScan(ctx context.Context, dir, cachePath string, post func(context.Context, []minidump.Summary) error) (int, error)`: a `post` that fails leaves the dumps pending on the next call; a `post` that succeeds commits them; more than 20 pending are posted in batches of 20; an empty `dir` posts nothing.

- [ ] **Step 6: Implement `tray/service/minidumps.go`:** `runMinidumpScan` plus `func (d *daemon) minidumpLoop(ctx context.Context)` — returns immediately when `minidump.Dir() == ""`; waits 2 minutes, then calls `runMinidumpScan(ctx, minidump.Dir(), filepath.Join(stateDir(), "minidumps.json"), d.client.PostMinidumps)` and repeats every hour; logs a failure with `logger.Warn` once per distinct error text and never exits on error. Start it with `go d.minidumpLoop(ctx)` where `rmmVersionLoop` is started.

- [ ] **Step 7: Run** `cd tray && go test -count=1 ./internal/minidump/ ./internal/api/ ./service/ && go vet ./... && GOOS=windows go build ./... && GOOS=darwin go build ./...` — Expected: PASS and clean builds.

- [ ] **Step 8: Commit** `git commit -am "Report decoded blue screen dumps from the tray app"` (after `git add` of the new files).

---

### Task 3: Table and repository

**Files:**
- Create: `migrations/<n>_asset_minidumps.sql` (number from the `/new-migration` skill), `app/repositories/asset_minidumps.py`
- Test: `tests/test_minidump_analysis.py`

**Interfaces:**
- Produces (all `async`, all parameterised, no f-string SQL; rows are dicts with `summary` and `analysis` decoded from their JSON columns):
  - `resolve_asset_for_device(device: Mapping[str, Any]) -> dict[str, Any] | None` — `{"id", "company_id", "name"}` of the device's asset in the device's company: `device["asset_id"]`, else `assets.tray_device_uid == device["device_uid"]`, else the single non-archived asset whose `serial_number` equals the device's (case-insensitive, non-empty). `None` when nothing or more than one matches.
  - `upsert_dump(*, company_id: int, asset_id: int, tray_device_id: int, record: Mapping[str, Any]) -> bool` — inserts; returns `False` when the unique key already exists (nothing is changed).
  - `prune(asset_id: int, keep: int = 50) -> int` — deletes the oldest rows beyond `keep`, never one with `analysis_status='analysing'`.
  - `list_for_asset(asset_id: int) -> list[dict[str, Any]]` — newest first by `COALESCE(crashed_at, file_modified_at, created_at)`.
  - `get_dump(dump_id: int) -> dict[str, Any] | None`.
  - `claim_analysis(dump_id: int, *, user_id: int | None, ticket_id: int | None, model: str, stale_before: datetime) -> bool` — one `UPDATE … SET analysis_status='analysing', analysis_started_at=…, analysis_error=NULL … WHERE id=? AND (analysis_status<>'analysing' OR analysis_started_at<?)` through `db.execute_rowcount`; `True` when a row changed.
  - `finish_analysis(dump_id: int, *, status: str, analysis: Mapping[str, Any] | None, error: str | None) -> None`.
  - `fail_stale(asset_id: int, stale_before: datetime) -> int`.

- [ ] **Step 1: Create the migration** with the `/new-migration` skill, name `asset_minidumps`, phase `expand`. Columns as in the spec's table, plus `analysis_started_at DATETIME NULL`. Types: `bugcheck_code BIGINT NULL`, `bugcheck_name VARCHAR(96) NULL`, `suspect_driver VARCHAR(64) NULL`, `file_name VARCHAR(255) NOT NULL`, `file_size BIGINT NOT NULL DEFAULT 0`, `file_modified_at DATETIME NOT NULL`, `crashed_at DATETIME NULL`, `supported TINYINT(1) NOT NULL DEFAULT 1`, `summary_json MEDIUMTEXT NULL`, `analysis_status VARCHAR(16) NOT NULL DEFAULT 'none'`, `analysis_json MEDIUMTEXT NULL`, `analysis_error VARCHAR(512) NULL`, `model VARCHAR(255) NULL`. `CREATE TABLE IF NOT EXISTS`, foreign keys to `companies` and `assets` with `ON DELETE CASCADE`, `UNIQUE KEY uq_asset_minidumps_file (tray_device_id, file_name, file_size, file_modified_at)`, `CREATE INDEX IF NOT EXISTS idx_asset_minidumps_asset ON asset_minidumps (asset_id, crashed_at)`. Follow `migrations/519_ai_health_checks.sql` for style.

- [ ] **Step 2: Write the test fixture and failing tests** in `tests/test_minidump_analysis.py`. Copy the `store` fixture pattern from `tests/test_ai_health_check.py` (in-memory aiosqlite, `Database()._adapt_sql_for_sqlite` on the new migration, `monkeypatch.setattr(module, "db", fake)`), with companies 1 and 2, assets 10 and 11 (company 1) and 20 (company 2), device 5 linked to asset 10, device 6 unlinked with asset 11's serial, device 8 unlinked matching nothing, and a `tickets` table. Tests:
  - `test_resolve_asset_for_device` — device 5 → asset 10; device 6 → asset 11 *(Review Focus 3)*; device 8 → `None`; a device in company 1 whose `asset_id` points at company 2's asset → `None`.
  - `test_upsert_is_idempotent` — the same record twice → `True` then `False`, one row.
  - `test_list_is_scoped_and_newest_first` — rows on assets 10 and 20 → `list_for_asset(10)` returns only asset 10's, newest crash first.
  - `test_prune_keeps_newest_and_running` — 55 rows, the oldest `analysing` → 50 remain plus the running one.
  - `test_claim_analysis_is_exclusive` — first claim `True`, second `False`; a claim with `stale_before` later than the first claim's start is `True`. *(Review Focus 4)*
  - `test_migration_is_idempotent` — run the adapted migration twice without error.

- [ ] **Step 3: Run** `pytest tests/test_minidump_analysis.py -q` — Expected: FAIL (module not found).

- [ ] **Step 4: Implement `app/repositories/asset_minidumps.py`** in the style of `app/repositories/ai_health_checks.py` (fixed column tuples, `_row` decoding, `_utcnow`). The insert that tolerates a duplicate uses the dialect-neutral pattern already used elsewhere in `app/repositories/` (find one with `grep -n "is_sqlite\|INSERT IGNORE\|ON CONFLICT" app/repositories/*.py`) and reports whether a row was written.

- [ ] **Step 5: Run** `pytest tests/test_minidump_analysis.py -q` — Expected: PASS.

- [ ] **Step 6: Commit** `git commit -m "Add storage for reported blue screen dumps"`.

---

### Task 4: Stop codes and ingest

**Files:**
- Create: `app/services/minidump_stop_codes.py`, `app/services/minidump_analysis.py`
- Test: `tests/test_minidump_analysis.py`

**Interfaces:**
- Consumes: Task 3's repository as `repo`.
- Produces:
  - `minidump_stop_codes.name(code: int | None) -> str` — e.g. `"DRIVER_IRQL_NOT_LESS_OR_EQUAL"`; `""` for an unknown code.
  - `minidump_stop_codes.label(code: int | None) -> str` — `"0x000000D1 DRIVER_IRQL_NOT_LESS_OR_EQUAL"`, or `"0x0000ABCD"` for an unknown code, `"Unknown"` for `None`.
  - `minidump_stop_codes.parameter_meanings(code: int | None) -> list[str]` — four short strings for codes where they are documented, else `[]`.
  - `minidump_analysis.clean_summary(raw: Any, *, now: datetime) -> dict[str, Any] | None` — the validated summary, or `None` when `file_name` or `file_modified_at` is unusable.
  - `async minidump_analysis.ingest(device: Mapping[str, Any], dumps: Any) -> dict[str, int]` — `{"received", "stored", "duplicates", "rejected"}`.
  - Constants `MAX_DUMPS_PER_UPLOAD = 20`, `MAX_SUMMARY_BYTES = 64 * 1024`, `KEEP_PER_ASSET = 50`.

- [ ] **Step 1: Write the failing tests:**
  - `test_stop_code_labels` — `label(0xD1) == "0x000000D1 DRIVER_IRQL_NOT_LESS_OR_EQUAL"`, `label(0x124) == "0x00000124 WHEA_UNCORRECTABLE_ERROR"`, `label(0xABCD) == "0x0000ABCD"`, `label(None) == "Unknown"`, `len(parameter_meanings(0xD1)) == 4`, `parameter_meanings(0xABCD) == []`.
  - `test_clean_summary_keeps_known_fields_only` — the contract example plus `"extra": "x"` and `"drivers"[0]["evil"]` → equal to the contract example.
  - `test_clean_summary_bounds_everything` — 2000 drivers, 500 stack drivers, a 5000-character driver name, `bugcheck_parameters` of 9 items, `bugcheck_code` of `"abc"`, `file_size` of `-1` → 512 drivers, 64 stack drivers, names ≤ 64 characters, 4 parameters, `bugcheck_code is None`, `file_size == 0`; `len(json.dumps(result)) <= MAX_SUMMARY_BYTES` (drivers are dropped from the end until it fits).
  - `test_clean_summary_rejects_bad_hex_and_names` — an address `"0xZZ"` or `"<script>"` becomes `""`; a driver name `"<b>x.sys"` becomes `"bx.sys"`; `file_name` `"..\\evil.dmp"` becomes `"evil.dmp"`; `file_name` `""` → `None`.
  - `test_clean_summary_fixes_implausible_times` — `crashed_at` `"1601-01-01T00:00:00Z"`, `""`, `"2040-01-01T00:00:00Z"` (with `now` 2026-11-11) and `"nonsense"` → `crashed_at == ""`; a `crashed_at` 2 hours ahead of `now` is kept. *(Review Focus 2)*
  - `test_ingest_stores_names_and_dedupes` — device 5, two dumps → `{"received": 2, "stored": 2, "duplicates": 0, "rejected": 0}`; rows carry `bugcheck_name`, `suspect_driver`, `company_id == 1`, `asset_id == 10`; the same upload again → `stored == 0, duplicates == 2`.
  - `test_ingest_uses_file_time_when_crash_time_missing` — `crashed_at == ""` → the row's `crashed_at` equals `file_modified_at`. *(Review Focus 2)*
  - `test_ingest_ignores_device_without_asset` — device 8 → `stored == 0`, no rows, no exception.
  - `test_ingest_rejects_non_list_and_caps_count` — `dumps="x"` → `received == 0`; 30 dumps → `received == 20`.
  - `test_ingest_prunes` — 55 dumps over three uploads → 50 rows.

- [ ] **Step 2: Run** `pytest tests/test_minidump_analysis.py -q` — Expected: the new tests FAIL.

- [ ] **Step 3: Implement `minidump_stop_codes.py`:** one dict `STOP_CODES: dict[int, tuple[str, tuple[str, ...]]]` of code → (name, parameter meanings) holding about 80 common bug check codes from Microsoft's *Bug Check Code Reference*, including at least `0x0A, 0x1A, 0x1E, 0x24, 0x3B, 0x3D, 0x50, 0x7A, 0x7B, 0x7E, 0x7F, 0x9F, 0xA0, 0xBE, 0xC1, 0xC2, 0xC4, 0xC5, 0xD1, 0xEF, 0xF4, 0xFC, 0x101, 0x109, 0x113, 0x116, 0x117, 0x119, 0x124, 0x133, 0x139, 0x13A, 0x144, 0x154, 0x15F, 0x18B, 0x19, 0x1C8, 0x1CA, 0xC000021A`. Parameter meanings are needed only where the reference documents them plainly.

- [ ] **Step 4: Implement `clean_summary` and `ingest`** in `minidump_analysis.py`. Hex strings must match `^0x[0-9a-f]{1,16}$`. Names keep `[A-Za-z0-9._-]` of the base file name. Timestamps are parsed as RFC 3339 and stored as naive UTC; plausible means `2000-01-01 <= t <= now + 1 day`. `ingest` calls `repo.resolve_asset_for_device`, then per dump `repo.upsert_dump`, then `repo.prune` once.

- [ ] **Step 5: Run** `pytest tests/test_minidump_analysis.py -q` — Expected: PASS.

- [ ] **Step 6: Commit** `git commit -m "Validate and store blue screen dump summaries"`.

---

### Task 5: Analysis

**Files:**
- Modify: `app/services/minidump_analysis.py`, `app/services/llm_usage.py` (add an `LLMFeature` after the `ai_health_check` entry)
- Test: `tests/test_minidump_analysis.py`

**Interfaces:**
- Consumes: `ai_troubleshooter._chat(llm, messages, json_mode=True, feature=…)`, `_search_kb(ticket_like, terms)`, `_extract_json_object`, `_string_list`, `_note_header`, `_link`; `troubleshoot_web_search.is_enabled()` / `research(terms)`; `ai_prompt_security.UntrustedRecord` and `build_messages` (see how `ai_health_check.run_report` assembles its messages and follow it); `tray_service.build_troubleshoot_llm_config`, `tray_service.add_troubleshoot_note`; `ai_consent.is_ai_allowed_for_ticket`; `tickets_repo.get_ticket`, `tickets_repo.list_ticket_assets`.
- Produces:
  - `class MinidumpError(Exception)` with `status_code: int` (default 409), like `ai_health_check.HealthCheckError`.
  - `LLM_FEATURE = "minidump_analysis"`; `STALE_AFTER = timedelta(minutes=15)`.
  - `research_terms(dump: Mapping[str, Any]) -> list[str]`.
  - `parse_report(data: Mapping[str, Any] | None, raw_text: str, *, allowed_sources: Sequence[Mapping[str, str]]) -> dict[str, Any]`.
  - `async start_analysis(*, dump: Mapping[str, Any], requested_by: Mapping[str, Any] | None, ticket_id: int | None = None) -> dict[str, Any]` — validates, claims, schedules `run_analysis`; returns `{"id", "analysis_status": "analysing", "ticket_id"}`.
  - `async run_analysis(dump_id: int, *, llm: Mapping[str, str], ticket_id: int | None) -> None`.
  - `report_note(dump: Mapping[str, Any], report: Mapping[str, Any], asset_name: str) -> str` — HTML for the ticket note.
  - `present(dump: Mapping[str, Any]) -> dict[str, Any]` — what templates and the API show (below).
  - `async list_for_asset(asset_id: int) -> list[dict[str, Any]]` — `repo.fail_stale` then `[present(d) for d in repo.list_for_asset(asset_id)]`.

  The report (`analysis_json`):

```json
{
  "likely_cause": "One paragraph.",
  "category": "driver",
  "suspect": "rtwlane.sys (Realtek wireless LAN driver)",
  "confidence": "medium",
  "evidence": ["…"],
  "steps": ["…"],
  "further_checks": ["…"],
  "sources": [{"title": "…", "url": "…"}],
  "web_error": "",
  "ticket_note": "posted"
}
```

  `category` ∈ `driver, hardware, memory, storage, firmware, windows, software, unknown` (anything else → `unknown`). `confidence` ∈ `high, medium, low` (anything else → `low`). Limits: `likely_cause` 2000 characters, `suspect` 200, lists 12 items of 500 characters, 8 sources. `ticket_note` ∈ `""`, `"posted"`, `"skipped: the requester has opted out of AI processing"`, `"failed"`.

  `present` returns: `id`, `file_name`, `crashed_at`, `stop_code` (`minidump_stop_codes.label`), `suspect_driver`, `uptime_label` (e.g. `"2 d 4 h"`, `""` when unknown), `supported`, `error`, `analysis_status`, `status_label` (`Not analysed`, `Analysing…`, `Analysed`, `Failed`), `status_variant` (`neutral`, `info`, `success`, `danger`), `analysis` (the report or `None`), `analysis_error`, `analysed_at`, `ticket_id`, `summary` (the stored summary, for the *Decoded dump* block).

- [ ] **Step 1: Write the failing tests** (the fixture stubs `tray_service.build_troubleshoot_llm_config`, `tray_service.add_troubleshoot_note`, `ai_troubleshooter._search_kb`, `troubleshoot_web_search.is_enabled`/`research`, and replaces the module's `_schedule` with one that records and closes the coroutine, as the health check tests do):
  - `test_research_terms` — bugcheck `0xD1` with suspect `rtwlane.sys` → `["DRIVER_IRQL_NOT_LESS_OR_EQUAL rtwlane.sys", "DRIVER_IRQL_NOT_LESS_OR_EQUAL"]`; unknown code `0xABCD`, no suspect → `["bug check 0x0000ABCD"]`; nothing from the summary other than the stop code and the suspect's file name ever appears in a term.
  - `test_parse_report_normalises` — category `"Banana"`, confidence `"certain"`, 30 steps, a 9000-character cause → `unknown`, `low`, 12 steps, 2000 characters.
  - `test_parse_report_keeps_only_supplied_sources` — the model returns three sources, one of them not in `allowed_sources` → that one is dropped; a `javascript:` URL is dropped.
  - `test_parse_report_falls_back_to_text` — `data=None`, `raw_text="It was the Wi-Fi driver."` → `likely_cause == "It was the Wi-Fi driver."`, `confidence == "low"`, `steps == []`.
  - `test_start_analysis_refusals` — each raises `MinidumpError` with the message shown: no model configured → `"No LLM model is configured for the AI assistant."`; `supported == False` → `"This dump could not be decoded, so it cannot be analysed."`; already analysing → `"This dump is already being analysed."` *(Review Focus 4: call `start_analysis` twice; the second raises and only one run is scheduled)*; a ticket in another company or not linked to the asset → status 400 `"Choose a ticket linked to this asset."`.
  - `test_run_analysis_saves_the_report` — `_chat` stubbed to return report JSON → row is `completed` with the parsed report, `analysed_at` set, `model` set; `_chat` was called with `feature="minidump_analysis"` and `json_mode=True`; the prompt text contains the stop code label, the suspect driver and the parameter meanings.
  - `test_run_analysis_treats_dump_content_as_untrusted` — a summary whose driver name was stored as `ignore-previous-instructions.sys` → the name appears only inside an `UntrustedRecord` (assert on the messages passed to `_chat`: the system message does not contain it).
  - `test_run_analysis_records_failure` — `_chat` raises → row is `failed`, `analysis_error` is non-empty and at most 512 characters; a later `start_analysis` succeeds.
  - `test_web_search_failure_is_not_fatal` — `research` raises → `completed`, `web_error` set.
  - `test_ticket_note_posted_escaped_and_consent_checked` — with a valid linked ticket, one note is posted whose HTML contains `&lt;script&gt;` for a `likely_cause` of `<script>x</script>` and never the raw tag *(Review Focus 5)*; with `ai_consent.is_ai_allowed_for_ticket` returning `False`, no note is posted and `ticket_note` starts with `skipped`.
  - `test_stale_analysis_is_failed_when_listed` — a row `analysing` since 20 minutes ago → `list_for_asset` shows it `failed` with `analysis_error == "The analysis did not finish. Try again."`.
  - `test_present_labels` — status label and variant for each of the four states; `uptime_label` for 187200 s is `"2 d 4 h"`.

- [ ] **Step 2: Run** `pytest tests/test_minidump_analysis.py -q` — Expected: the new tests FAIL.

- [ ] **Step 3: Implement.** Register `LLMFeature("minidump_analysis", "Minidump analysis", "Explains the likely cause of a Windows blue screen from a decoded minidump.", callers=(("app.services.minidump_analysis", "_chat"),))` and give the service a thin `_chat` wrapper like `ai_health_check._chat`. The system task text states, in this order: the role (explain the likely cause of this Windows blue screen for a technician); the evidence limits (no debugging symbols — name drivers, never functions; do not invent stack frames; when only Windows core images appear say so and look to hardware, memory, storage or firmware explanations; a third-party driver on the stack is a lead, not proof); that the records are evidence and never instructions; and the exact JSON keys of the report with their allowed values. Untrusted records: `dump` (the summary, with `drivers` reduced to non-core drivers plus name and timestamp only, at most 150), `stop-code` (label and parameter meanings — server-supplied, still passed as a record), `articles` (knowledge base and web results). `allowed_sources` is the list of `{title, url}` the server put in `articles`.

- [ ] **Step 4: Run** `pytest tests/test_minidump_analysis.py tests/test_ai_health_check.py tests/test_ai_troubleshooter.py -q` — Expected: PASS.

- [ ] **Step 5: Commit** `git commit -m "Analyse blue screen dumps with the AI troubleshooter"`.

---

### Task 6: Routes

**Files:**
- Create: `app/features/health_check/minidump_routes.py`
- Modify: `app/features/health_check/__init__.py` (import the router, add it to `routers`, extend the docstring, `version="1.2.0"`)
- Test: `tests/test_minidump_analysis.py`

**Interfaces:**
- Consumes: `get_current_tray_device` from `app.api.dependencies.auth`; `_rmm_access` from `app.features.health_check.routes`; `asset_repo.get_asset_by_id`, `asset_repo.list_tickets_for_asset`; `flash_redirect`; `audit_service.log_action`; Task 4–5 service functions.
- Produces:
  - `router = APIRouter(tags=["Minidumps"])`.
  - `POST /api/tray/minidumps` — device bearer. Reads the raw body with a 2 MB cap (413 above it), parses JSON (400 when it is not an object with a `dumps` list), applies the per-device throttle (429), returns `{"status": "ok", **counts}`.
  - `GET /api/assets/{asset_id}/minidumps` → `{"dumps": [present…]}`.
  - `POST /api/assets/{asset_id}/minidumps/{dump_id}/analyse` — JSON body `{"ticket_id": int | null}` → 202 with `start_analysis`'s result.
  - `POST /rmm/assets/{asset_id}/minidumps/{dump_id}/analyse` — form (`ticket_id` optional), CSRF as for the health check form; redirects to `/assets/{asset_id}#minidumps` with a success or error flash.
  - `async asset_context(asset: dict[str, Any], *, can_run: bool) -> dict[str, Any] | None` for the asset page: `None` unless `repo`-resolved tray device exists for the asset (use `ai_health_checks.find_tray_device_for_asset`) and `ai_troubleshooter.endpoint_platform(device, asset) == "windows"`, or dumps already exist for the asset. Otherwise `{"dumps", "count", "can_run", "llm_ready", "tickets"}` where `tickets` are the asset's tickets whose status is not closed or resolved (`id`, `subject`), `llm_ready` is whether `build_troubleshoot_llm_config(None)["model"]` is set.
  - Throttle: `allow_upload(device_id: int, now: float) -> bool` in the routes module — at most 6 uploads per device per 10 minutes, kept in a module-level dict that drops entries older than the window.

- [ ] **Step 1: Write the failing tests.** Build a `FastAPI()` app with the router, override `get_current_tray_device`, and monkeypatch the routes module's `_rmm_access` to return a chosen `(user, company_id, can_run, None)` or raise `HTTPException(403)` (follow how existing route tests in `tests/` drive a feature-pack router — find one with `grep -ln "dependency_overrides" tests/ | head`):
  - `test_device_upload_stores_dumps` — 200, `stored == 1`, row on asset 10.
  - `test_device_upload_rejects_oversize_and_malformed` — a 2 MB + 1 body → 413; `[]` → 400; `{"dumps": "x"}` → 400.
  - `test_device_upload_requires_device_token` — without the override, no bearer → 401 or 403 (whichever `get_current_tray_device` raises), nothing stored.
  - `test_device_upload_is_throttled` — the 7th upload inside 10 minutes → 429; `allow_upload` with `now` 601 seconds later → `True`.
  - `test_list_requires_access_and_scopes` — `_rmm_access` raising 403 → 403; allowed for company 1 → asset 10's dumps only; `asset_id=20` with `company_id=1` → 404.
  - `test_analyse_checks_dump_belongs_to_asset` — a dump of asset 11 posted under asset 10 → 404, nothing claimed.
  - `test_analyse_needs_write_access` — `_rmm_access` called with `write=True` (assert the keyword) and its 403 is returned.
  - `test_analyse_starts_and_audits` — 202, `analysis_status == "analysing"`, one `audit_service.log_action` call with `action="minidump.analyse"`, `entity_type="asset_minidump"`.
  - `test_analyse_form_redirects_with_flash` — success → 303 to `/assets/10#minidumps`; a `MinidumpError` → 303 to the same place (error flash).
  - `test_asset_context_visibility` — a macOS-only asset with no dumps → `None`; a Windows tray device → context with `count`; no tray device but stored dumps → context.

- [ ] **Step 2: Run** `pytest tests/test_minidump_analysis.py -q` — Expected: the new tests FAIL.

- [ ] **Step 3: Implement the routes and register the router.** Every staff route calls `_rmm_access(request, asset_id=asset_id, write=…)`, loads the asset and returns 404 unless `asset["company_id"] == company_id`, then (for analyse) 404 unless `dump["asset_id"] == asset_id`. Give every route a `summary`, a docstring and a Pydantic response model so it appears in Swagger.

- [ ] **Step 4: Run** `pytest tests/test_minidump_analysis.py -q && python -c "from app.features.health_check import PACK; print(PACK.version)"` — Expected: PASS and `1.2.0`.

- [ ] **Step 5: Commit** `git commit -m "Add routes for blue screen dump upload and analysis"`.

---

### Task 7: Asset page section

**Files:**
- Create: `app/templates/assets/_minidumps.html`, `app/static/js/asset_minidumps.js`
- Modify: `app/features/assets/routes.py` (beside the `health_check_context` block near line 2067, and the template context near line 2136), `app/templates/assets/detail.html` (nav list after the `health-check` entry; include the partial after the health check section; load the script where the page's other scripts load), `app/static/css/app.css` (`.md-*` rules after the `.ce-*` block)
- Test: `tests/test_minidump_analysis.py`

**Interfaces:**
- Consumes: `minidump_routes.asset_context` (Task 6).
- Produces: template variable `minidumps` (the context or `None`); section id `minidumps`, `data-asset-edit-section="minidumps"`; nav entry `("minidumps", "minidumps", "Minidumps", minidumps.count or "", minidumps)`.

- [ ] **Step 1: Read first:** `docs/wiki/developer/Design Guidelines.md` (sections *Multi-section settings workspace* and *Modals*), the health check section of `detail.html` (lines 462–511) and `asset_edit_sections.js`. The new section must look and behave like its neighbours.

- [ ] **Step 2: Write the failing tests** (render `assets/_minidumps.html` through the app's Jinja environment with a hand-built `minidumps` context, the way existing template tests do — find one with `grep -ln "get_template" tests/ | head`):
  - `test_section_empty_state` — `dumps == []` → contains `No blue screens reported yet`, no `<form`.
  - `test_section_lists_dump_with_chips_and_form` — one unanalysed dump → its stop code label, suspect driver, a `data-utc` crash time, status `Not analysed`, a form posting to `/rmm/assets/10/minidumps/3/analyse` containing the CSRF partial's field and a `ticket_id` select with the offered ticket.
  - `test_section_escapes_model_and_device_text` — `likely_cause` `<script>alert(1)</script>` and suspect `<img src=x>` → the output contains `&lt;script&gt;` and no `<script>alert`. *(Review Focus 5)*
  - `test_section_hides_analyse_when_not_allowed` — `can_run == False` → no form; `llm_ready == False` → the button is `disabled` with the text `No AI model is configured`; `supported == False` → the dump's `error` is shown and there is no form.
  - `test_section_shows_running_state` — `analysis_status == "analysing"` → status `Analysing…`, the button is disabled, and the card carries `data-md-status="analysing"`.
  - `test_asset_page_wires_the_section` — `detail.html` source contains the nav tuple key `"minidumps"`, `{% include "assets/_minidumps.html" %}` and `asset_minidumps.js`.

- [ ] **Step 3: Run** `pytest tests/test_minidump_analysis.py -q` — Expected: the new tests FAIL.

- [ ] **Step 4: Add the context** in `app/features/assets/routes.py`: `minidumps_context = None`; when `rmm_context` is set, the page is not `customer_safe` and `main_module._feature_pack_available("health_check")`, call `minidump_routes.asset_context(dict(record), can_run=bool(rmm_context.get("can_run")))`. Pass it as `"minidumps"`. (Unlike the health check it does not require the RMM agent — only the tray app.)

- [ ] **Step 5: Write the partial.** A `<section class="card card--panel ce-section" id="minidumps" data-asset-edit-section="minidumps" aria-labelledby="asset-minidumps-heading" data-md-root data-md-url="/api/assets/{{ asset.id }}/minidumps">` with:
  - Title `Minidumps`; subtitle `Blue screen crashes found on this computer by its tray app. Analyse one to have the AI troubleshooter explain the likely cause and how to fix it.`
  - A `role="status" aria-live="polite"` line (`data-md-live`), empty by default.
  - A list of `.md-dump` cards, each: crash time (`<span data-utc>`), `.md-chip` chips for the stop code, the suspect driver (`Suspected driver: …`, or `No third-party driver identified`) and `Up … before the crash`; a `<span class="status status--{{ variant }}">`; the Analyse form (`Analyse` or `Analyse again`, the `Also post to ticket` select labelled `optional` with a first option `Don't post to a ticket`); a failed analysis shows `analysis_error`.
  - A completed analysis in `<details class="md-result" open>`: `Likely cause`, chips for confidence (`High confidence` …) and category, `Evidence` (`<ul>`), `What to do` (`<ol>`), `Further checks` (`<ul>`), `Sources` (links with `rel="noopener noreferrer"`, only `http`/`https` URLs or `/`-relative paths), a muted line with `analysed_at` (`data-utc`), and — when `ticket_id` — a link to `/admin/tickets/{{ ticket_id }}` with the `ticket_note` state.
  - A collapsed `<details class="md-raw">` titled `Decoded dump`: parameters (with their meanings when known), faulting address, drivers on the stack as `driver+offset`, and a count of loaded drivers. Wide content scrolls inside the block, never the page.
  - Empty state: `No blue screens reported yet. When this computer has a blue screen, its tray app reports the crash here automatically after the restart. If a crash is missing, check that Windows is set to write small memory dumps and that the tray app is up to date.`

- [ ] **Step 6: Add the CSS** with the `.md-` prefix using only existing tokens (`var(--space-…)`, `var(--radius-…)`, `var(--font-size-…)`, `var(--color-…)`), no literal colours; cards stack in one column and chips wrap below 640 px.

- [ ] **Step 7: Write `asset_minidumps.js`:** on `DOMContentLoaded`, find `[data-md-root]`; while any `[data-md-status="analysing"]` exists, fetch `data-md-url` every 5 seconds (stop after 20 minutes or when the tab is hidden; resume on `visibilitychange`); when a dump leaves `analysing`, set the live line to `Analysis finished.` or `Analysis failed.` and reload the page with the `#minidumps` hash. No inline script, no globals; network errors are ignored until the next tick.

- [ ] **Step 8: Run** `pytest tests/test_minidump_analysis.py tests/test_asset_page_performance.py tests/test_asset_detail_custom_fields.py -q` — Expected: PASS. If `test_asset_page_performance.py` counts queries, the new context must stay within its budget; if the budget must rise, raise it by the exact number of queries added and say so in the commit message.

- [ ] **Step 9: Look at it.** Start the app (`python -m uvicorn app.main:app`) on the SQLite fallback, insert a tray device and three `asset_minidumps` rows for an asset (one unanalysed, one completed with a full report, one failed), and screenshot `/assets/{id}#minidumps` at 1440 px and 390 px wide, in light and dark themes, with Playwright. Confirm: nothing overflows the viewport, the nav entry shows the count, the section matches the health check section's spacing. Fix what is off.

- [ ] **Step 10: Commit** `git commit -m "Show blue screen dumps and their analysis on the asset page"`.

---

### Task 8: Documentation, change log and final verification

**Files:**
- Create: `docs/wiki/administration/Minidump Analysis.md`, `changes/<guid>.json` (via `/changelog-entry`)
- Modify: `docs/wiki/administration/Health Check.md` (a *See also* line), `docs/wiki/getting-started/Tray App.md` (the endpoint table near line 185 gains `/api/tray/minidumps`; a short subsection under §11), `docs/api/tray.md` if it lists device endpoints

- [ ] **Step 1: Write the wiki page** for technicians and admins: what the section shows; how a dump gets there (after the restart, then hourly; newest 20; Windows must write small memory dumps — say where that setting is); what **Analyse** does and what it sends to the AI model and, when web search is on, to the search provider (stop code name and driver file name only); that the dump file never leaves the computer; how to read the result (confidence, why a driver is named rather than a function); the limits (64-bit minidumps only, no `MEMORY.DMP`, no symbols); who can use it (RMM scripts access; write access to analyse); the routes; and troubleshooting (nothing listed, "could not be decoded", analysis failed).

- [ ] **Step 2: Add the change log entry** with the `/changelog-entry` skill: type `feature`, summary `Added a Minidumps section to the asset page: the tray app reports Windows blue screen dumps and the AI troubleshooter explains the likely cause.`

- [ ] **Step 3: Audit SQL.** `grep -n "f\"\|\.format(\|% (" app/repositories/asset_minidumps.py app/services/minidump_analysis.py app/features/health_check/minidump_routes.py` — Expected: no SQL built with interpolation.

- [ ] **Step 4: Run everything touched.**
  - `pytest tests/test_minidump_analysis.py tests/test_ai_health_check.py tests/test_ai_troubleshooter.py tests/test_change_log_service.py -q` — Expected: PASS.
  - `pytest -q -x` (full suite, as the project rules require before submitting) — Expected: PASS; investigate any failure rather than skipping it.
  - `cd tray && go test -count=1 ./... && go vet ./...` — Expected: PASS.

- [ ] **Step 5: Commit** `git commit -m "Document blue screen dump analysis"` and push with `git push -u origin claude/focused-darwin-s7cg6a`.

---

## After the last task

- The parser has only met synthetic dumps. Before the feature is relied on, run `MYPORTAL_TEST_MINIDUMP=<path to a real .dmp> go test -run TestRealDump ./internal/minidump/` on a real blue screen dump and compare the stop code, suspect driver and driver count with WinDbg's `!analyze -v` for the same file. Report the result to the owner; do not claim the parser is verified until this has been done.
- A pull request is opened only when the owner asks for one.
