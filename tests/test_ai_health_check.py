"""AI health check: snapshot rules, the device round trips, triage, the report and the pages."""
from __future__ import annotations

import gzip
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest

from app.core.database import Database
from app.repositories import ai_health_checks as repo
from app.repositories import ai_required_logs as required_repo
from app.repositories import tray as tray_repo
from app.services import ai_health_check as hc
from app.services import ai_required_logs
from app.services import ai_troubleshooter
from app.services import tray as tray_service
from tests.test_rmm_scripts import _SqliteDb, _environment, anyio_backend  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]
LLM = {"base_url": "http://llm", "model": "test-model", "api_key": ""}

WINDOWS_SNAPSHOT = {
    "schema": 1,
    "platform": "windows",
    "is_virtual": False,
    "sections": {
        "system": {"os": "Microsoft Windows 11 Pro", "uptime_hours": 900, "build": "22631"},
        "cpu": {"load_percent": 12, "percent_performance_limit": 60, "dpc_percent": 1, "logical_processors": 8},
        "memory": {"used_percent": 70, "commit_percent": 92, "total_mb": 16000, "pages_per_sec": 20},
        "volumes": [
            {"drive": "C:", "free_percent": 3.5, "free_gb": 8, "dirty": True, "health": "Healthy", "boot_volume": True},
            {"drive": "D:", "free_percent": 50, "dirty": False, "health": "Healthy"},
        ],
        "disks": [
            {"device_id": "0", "name": "NVMe SSD", "media_type": "SSD", "health": "Healthy",
             "operational_status": ["OK"], "wear_percent": 72, "temperature_c": 74, "read_errors_uncorrected": 0},
            {"device_id": "1", "name": "Old HDD", "media_type": "HDD", "health": "Warning",
             "operational_status": ["Predictive Failure"], "read_errors_uncorrected": 3},
        ],
        "smart_predictions": [{"instance": "IDE\\DiskOld", "predict_failure": True, "reason": 5}],
        "chkdsk": {"ntfs_errors": 2, "disk_errors": 12, "scheduled": True},
        "defrag": {"scheduled_task": {"enabled": False}, "trim_enabled": True,
                   "analysis": [{"drive": "D:", "defrag_recommended": True, "total_fragmentation_percent": 18}]},
        "thermal": {"zones": [{"zone": "TZ0", "celsius": 91, "passive_limit_percent": 80}], "thermal_events": 3},
        "stability": {"window_days": 30, "bugcheck_count": 2, "unexpected_shutdowns": 1, "whea_errors": 4,
                      "app_crashes": 15, "service_crashes": 0},
        "drivers": {"problem_count": 1},
        "updates": {"reboot_pending": {"windows_update": True}, "failed_updates": 2,
                    "last_hotfix": {"installed_on": (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")}},
        "security": {"defender": {"realtime": False, "signature_age_days": 10}, "antivirus_products": [],
                     "firewall": [{"profile": "Public", "enabled": False}]},
        "battery": {"present": True, "health_percent": 45},
        "component_store": {"state": "repairable"},
        "performance": {"boots": [{"boot_ms": 150000}], "stability_index": 3.2},
    },
    "sensors": {"status": "available", "readings": [
        {"hardware_type": "Cpu", "hardware": "Intel Core", "type": "Temperature", "name": "Package", "value": 97},
        {"hardware_type": "GpuNvidia", "hardware": "RTX", "type": "Temperature", "name": "Core", "value": 70},
        {"hardware_type": "Motherboard", "hardware": "Board", "type": "Fan", "name": "CPU fan", "value": 0},
    ]},
    "pawnio": {"status": "installed_and_removed", "detail": "version 2.1.0"},
    "errors": {},
}


# --------------------------------------------------------------------------- #
# Rules over the snapshot
# --------------------------------------------------------------------------- #


def _titles(signals):
    return [signal["title"] for signal in signals]


def test_rules_flag_drive_failure_chkdsk_defrag_thermals_and_crashes():
    signals = hc.evaluate_snapshot(WINDOWS_SNAPSHOT)
    titles = " | ".join(_titles(signals))
    for expected in (
        "C: is almost full (3.5% free)",
        "C: is marked dirty",
        "Old HDD is failing or about to fail",
        "Old HDD has 3 uncorrected read/write errors",
        "A drive's SMART data predicts it will fail",
        "12 disk I/O errors recently",
        "NVMe SSD is 72% worn out",
        "NVMe SSD is running hot (74 °C)",
        "2 NTFS file system warnings or errors recently",
        "A disk check is scheduled for the next restart",
        "Scheduled drive optimisation is turned off",
        "D: is fragmented (18%)",
        "TZ0 is at 91 °C",
        "TZ0 is throttling the CPU to 80%",
        "CPU performance is limited to 60%",
        "CPU temperature is 97 °C",
        "CPU fan reads 0 RPM while the CPU is hot",
        "2 blue screen crashes in 30 days",
        "4 hardware (WHEA) errors in 30 days",
        "15 application crashes in 30 days",
        "Committed memory is at 92% of the limit",
        "Not restarted for 38 days",
        "A restart is waiting to finish installing updates",
        "No updates installed for 90 days",
        "Real-time antivirus protection is off",
        "The Public firewall profile is off",
        "Battery holds 45% of its design capacity",
        "The Windows component store is damaged but repairable",
        "Last boot took 150 seconds",
        "Reliability Monitor stability index is 3.2 of 10",
    ):
        assert expected in titles, expected
    # Most severe first.
    ranks = [hc.SEVERITY_RANK[signal["severity"]] for signal in signals]
    assert ranks == sorted(ranks)
    value, overall = hc.score(signals)
    assert overall == "critical" and value == 0


def test_rules_on_a_healthy_or_empty_snapshot():
    healthy = {"sections": {"volumes": [{"drive": "C:", "free_percent": 60, "dirty": False, "health": "Healthy"}],
                            "disks": [{"name": "SSD", "health": "Healthy", "operational_status": ["OK"], "wear_percent": 3}],
                            "security": {"defender": {"realtime": True, "signature_age_days": 0}}}}
    assert hc.evaluate_snapshot(healthy) == []
    assert hc.score([]) == (100, "healthy")
    assert hc.evaluate_snapshot({}) == [] and hc.evaluate_snapshot(None) == []
    # Odd values never break the rules.
    assert hc.evaluate_snapshot({"sections": {"volumes": "x", "disks": [None, 3], "cpu": {"load_percent": "high"}}}) == []


def test_macos_rules():
    signals = hc.evaluate_snapshot({"sections": {
        "thermal": {"cpu_speed_limit": 70},
        "stability": {"window_days": 30, "panic_count": 1},
        "battery": {"present": True, "condition": "Service Recommended", "health_percent": 78},
        "disks": [{"name": "APPLE SSD", "smart_status": "Failing"}],
    }})
    titles = _titles(signals)
    assert "macOS is limiting the CPU to 70% of its speed" in titles
    assert "1 kernel panics in 30 days" in titles
    assert "Battery condition: Service Recommended" in titles
    assert "SMART reports APPLE SSD is failing" in titles


def test_other_antivirus_suppresses_defender_flags():
    snapshot = {"sections": {"security": {"defender": {"realtime": False, "signature_age_days": 30},
                                          "antivirus_products": [{"name": "Windows Defender"}, {"name": "Huntress EDR"}]}}}
    assert hc.evaluate_snapshot(snapshot) == []


# --------------------------------------------------------------------------- #
# Snapshot parsing and logs
# --------------------------------------------------------------------------- #


def test_snapshot_is_parsed_bounded_and_redacted():
    raw = json.dumps({
        "sections": {"stability": {"bugchecks": [{"message": "Owner admin@example.com password=hunter2 at 10.1.2.3"}]},
                     "big": list(range(1000))},
        "nan": float("inf"),
    })
    snapshot = hc.parse_snapshot(raw.encode())
    message = snapshot["sections"]["stability"]["bugchecks"][0]["message"]
    assert "admin@example.com" not in message and "hunter2" not in message and "10.1.2.3" not in message
    assert len(snapshot["sections"]["big"]) == hc._MAX_SNAPSHOT_LIST
    assert snapshot["nan"] is None
    assert hc.parse_snapshot(b"not json") == {} and hc.parse_snapshot(b"[1]") == {} and hc.parse_snapshot(None) == {}


def test_log_samples_note_unavailable_sources():
    text = (
        "### System Log\n2026-10-22 10:00:00 [Error] id=7 disk: bad block on \\Device\\Harddisk1 user@example.com\n\n"
        "### Microsoft-Windows-Kernel-WHEA/Errors Log\n[unavailable: The specified channel could not be found.]\n\n"
        "### file:C:\\Windows\\Logs\\CBS\\CBS.log Log\n[no matching log files found]\n\n"
    )
    requests = [{"source": "System", "reason": "disk errors"},
                {"source": "Microsoft-Windows-Kernel-WHEA/Errors", "reason": "hardware errors"},
                {"source": "file:C:\\Windows\\Logs\\CBS\\*.log", "reason": "servicing"}]
    samples = hc.collect_log_samples(text, requests, phase="snapshot")
    by_source = {sample["source"]: sample for sample in samples}
    assert by_source["System"]["unavailable"] is None and "[REDACTED_EMAIL]" in by_source["System"]["text"]
    assert by_source["Microsoft-Windows-Kernel-WHEA/Errors"]["unavailable"] == "The specified channel could not be found."
    cbs = by_source["file:C:\\Windows\\Logs\\CBS\\CBS.log"]
    assert cbs["reason"] == "servicing" and cbs["unavailable"].startswith("No matching log files")


def test_baseline_logs_are_allowed_and_fit_the_agent_limit():
    for platform in ("windows", "macos"):
        requests = hc.baseline_log_requests(platform)
        assert 0 < len(requests) <= 8
        allowed = ai_troubleshooter.allowed_log_sources(platform)
        for request in requests:
            source = request["source"]
            assert source in allowed or ai_troubleshooter.normalise_file_source(source, platform) == source, source
            assert 1 <= request["hours"] <= ai_troubleshooter.MAX_LOG_HOURS
    assert hc.baseline_log_requests("unknown") == []


def test_snapshot_script_reports_the_values_the_rules_read():
    """The rules and the report read keys the Windows script must produce."""

    script = (ROOT / "tray/internal/healthcheck/health_windows.ps1").read_text(encoding="utf-8")
    assert script.isascii()
    for section in ("system", "cpu", "memory", "volumes", "disks", "smart_predictions", "chkdsk", "defrag",
                    "thermal", "performance", "stability", "drivers", "services", "updates", "security",
                    "battery", "network", "power", "component_store"):
        assert f"Add-Section '{section}'" in script, section
    for key in ("free_percent", "free_gb", "dirty", "boot_volume", "health", "operational_status", "wear_percent",
                "temperature_c", "read_errors_uncorrected", "write_errors_uncorrected", "predict_failure",
                "ntfs_errors", "disk_errors", "scheduled", "trim_enabled", "defrag_recommended",
                "total_fragmentation_percent", "passive_limit_percent", "thermal_events", "load_percent",
                "percent_performance_limit", "dpc_percent", "interrupt_percent", "processor_queue_length",
                "logical_processors", "used_percent", "commit_percent", "pages_per_sec", "total_mb", "boot_ms",
                "stability_index", "bugcheck_count", "unexpected_shutdowns", "whea_errors", "app_crashes",
                "service_crashes", "problem_count", "uptime_hours", "reboot_pending", "installed_on",
                "failed_updates", "realtime", "signature_age_days", "health_percent", "window_days"):
        assert re.search(r"\b" + key + r" = ", script), key
    macos = (ROOT / "tray/internal/healthcheck/snapshot_darwin.go").read_text(encoding="utf-8")
    for key in ("cpu_speed_limit", "panic_count", "smart_status", "condition", "health_percent", "free_percent"):
        assert f'"{key}"' in macos, key


def test_pawnio_installer_hash_is_pinned_the_same_everywhere():
    go = (ROOT / "tray/internal/healthcheck/pawnio.go").read_text(encoding="utf-8")
    build = (ROOT / "tray/sensor-bundle/build.py").read_text(encoding="utf-8")
    notice = (ROOT / "tray/internal/healthcheck/sensorbundle/NOTICE.txt").read_text(encoding="utf-8")
    pinned = re.search(r'pawnIOInstallerSHA256 = "([0-9a-f]{64})"', go).group(1)
    assert f'PAWNIO_INSTALLER_SHA256 = "{pinned}"' in build
    assert pinned in notice


def test_sensor_bundle_build_rejects_a_changed_installer(monkeypatch):
    import hashlib
    import importlib.util

    spec = importlib.util.spec_from_file_location("tray_sensor_bundle", ROOT / "tray/sensor-bundle/build.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        builder.verify_installer(b"unverified installer")
    monkeypatch.setattr(builder, "PAWNIO_INSTALLER_SHA256", hashlib.sha256(b"ok").hexdigest())
    assert builder.verify_installer(b"ok") == b"ok"


def test_report_parsing_bounds_and_normalises_model_output():
    data = {
        "overall_status": "Critical!", "summary": "<b>Disk failing</b>",
        "areas": [{"area": "Drives", "status": "critical", "summary": "Back up"}, {"area": "??", "status": "odd"}],
        "findings": [{"title": "HDD failing", "severity": "urgent", "area": "disk", "evidence": "x" * 5000}],
        "steps": [{"action": "Back up the data", "priority": "now", "area": "storage",
                   "reference": "https://learn.microsoft.com/a"},
                  {"action": "Replace the drive", "priority": "whenever", "reference": "javascript:alert(1)"},
                  "Run chkdsk"],
        "required_logs": [{"source": "Microsoft-Windows-Kernel-EventTracing/Admin", "reason": "ETW"}],
    }
    report = hc._parse_report(data, "")
    assert report["overall_status"] is None and report["summary"] == "Disk failing"
    assert report["areas"][0] == {"area": "storage", "status": "critical", "summary": "Back up"}
    assert report["areas"][1]["area"] == "os" and report["areas"][1]["status"] == "unknown"
    assert report["findings"][0]["severity"] == "medium" and len(report["findings"][0]["evidence"]) == 1500
    assert report["steps"][0]["reference_url"] == "https://learn.microsoft.com/a"
    assert report["steps"][1]["priority"] == "soon" and report["steps"][1]["reference_url"] == ""
    assert report["steps"][2]["action"] == "Run chkdsk"
    fallback = hc._parse_report(None, "plain answer")
    assert fallback["summary"] == "plain answer" and fallback["steps"] == []


# --------------------------------------------------------------------------- #
# The full flow against SQLite
# --------------------------------------------------------------------------- #


@pytest.fixture
async def store(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript("""
        CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE assets (id INTEGER PRIMARY KEY, company_id INT, name TEXT, serial_number TEXT, os_name TEXT,
          archived_at TEXT, tray_device_uid TEXT);
        CREATE TABLE tray_devices (id INTEGER PRIMARY KEY, company_id INT, asset_id INT, device_uid TEXT, os TEXT,
          os_version TEXT, hostname TEXT, serial_number TEXT, agent_version TEXT, status TEXT DEFAULT 'active',
          last_seen_utc TEXT);
        CREATE TABLE tray_command_log (id INTEGER PRIMARY KEY, device_id INT, command TEXT, payload_json TEXT,
          initiated_by_user_id INT, status TEXT DEFAULT 'queued', error TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP, delivered_at TEXT);
        INSERT INTO companies VALUES (1, 'Contoso'), (2, 'Other');
        INSERT INTO assets (id, company_id, name, serial_number, os_name) VALUES
          (10, 1, 'PC-01', 'SN1', 'Windows 11 Pro'), (11, 1, 'PC-02', 'SN2', 'Windows 11 Pro'),
          (20, 2, 'OTHER', 'SN3', 'Windows 11 Pro');
        INSERT INTO tray_devices (id, company_id, asset_id, device_uid, os, hostname, serial_number, status) VALUES
          (5, 1, 10, 'dev-1', 'windows', 'pc-01', 'SN1', 'active'),
          (6, 1, NULL, 'dev-2', 'windows', 'pc-02', 'sn2', 'active'),
          (7, 2, 20, 'dev-3', 'windows', 'other', 'SN3', 'revoked');
    """)
    await conn.executescript(Database()._adapt_sql_for_sqlite(
        (ROOT / "migrations" / "519_ai_health_checks.sql").read_text(encoding="utf-8")))
    fake = _SqliteDb(conn)
    for module in (repo, required_repo, tray_repo):
        monkeypatch.setattr(module, "db", fake)
    sent: list[tuple[str, dict]] = []

    async def send_to_device(device_uid, payload):
        sent.append((device_uid, payload))
        return True

    notes: list[tuple[int, str]] = []

    async def add_note(ticket_id, body_html):
        notes.append((ticket_id, body_html))
        return {"id": len(notes)}

    monkeypatch.setattr(tray_service, "send_to_device", send_to_device)
    monkeypatch.setattr(tray_service, "add_troubleshoot_note", add_note)
    monkeypatch.setattr(tray_service, "build_troubleshoot_llm_config", lambda model=None: dict(LLM, model=model or "test-model"))
    monkeypatch.setattr(ai_required_logs, "_approvals", ai_required_logs._ApprovalCache())
    scheduled: list = []

    def schedule(coro, *, description, health_check_id):
        scheduled.append((description, health_check_id))
        coro.close()

    monkeypatch.setattr(hc, "_schedule", schedule)
    # Assets with an RMM agent deployed (the health check is an RMM option).
    agents = {10: {"id": 1, "asset_id": 10, "company_id": 1}, 11: {"id": 2, "asset_id": 11, "company_id": 1}}

    async def get_asset_agent(asset_id):
        return agents.get(asset_id)

    monkeypatch.setattr(hc.rmm_repo, "get_asset_agent", get_asset_agent)
    yield {"db": fake, "sent": sent, "notes": notes, "scheduled": scheduled, "agents": agents}
    await conn.close()


def _bundle(text: str) -> bytes:
    return gzip.compress(text.encode())


async def _device(device_id: int) -> dict:
    return await tray_repo.get_device_by_id(device_id)


@pytest.mark.anyio
async def test_device_lookup_matches_linked_or_unlinked_devices(store):
    assert (await repo.find_tray_device_for_asset(10))["device_uid"] == "dev-1"
    # Unlinked device with the asset's serial number (case-insensitive).
    assert (await repo.find_tray_device_for_asset(11))["device_uid"] == "dev-2"
    # Revoked devices never match.
    assert await repo.find_tray_device_for_asset(20) is None


@pytest.mark.anyio
async def test_full_health_check_flow(store, monkeypatch):
    monkeypatch.setattr(hc.get_settings(), "health_check_pawnio_enabled", True)
    asset = {"id": 10, "company_id": 1, "name": "PC-01", "os_name": "Windows 11 Pro"}
    started = await hc.start_health_check(asset=asset, device=await _device(5), requested_by={"id": 9})
    assert started["delivered"] and started["allow_pawnio"] and started["status"] == "collecting"
    uid, payload = store["sent"][0]
    assert uid == "dev-1" and payload["type"] == "health_check" and payload["phase"] == "snapshot"
    assert payload["health_check_id"] == started["id"] and payload["allow_pawnio"] is True
    assert [r["source"] for r in payload["log_requests"]] == [r["source"] for r in hc.BASELINE_LOG_REQUESTS["windows"]]
    assert "server_context" not in payload and payload["extra_event_logs"] == []
    command = await tray_repo.get_command(started["command_id"])
    assert command["command"] == "health_check" and command["status"] == "delivered"

    # Only one check per asset at a time.
    with pytest.raises(hc.HealthCheckError, match="already running"):
        await hc.start_health_check(asset=asset, device=await _device(5), requested_by={"id": 9})

    check = await repo.get(started["id"])
    await hc.record_device_status(check, command, "reading_sensors", "Installing PawnIO")
    assert (await repo.get(started["id"]))["stage_message"].startswith("Reading hardware sensors: Installing PawnIO")

    # The device uploads the snapshot and logs; WHEA is missing on this computer.
    logs = ("### System Log\n2026-10-22 10:00:00 [Error] id=7 disk: bad block\n\n"
            "### Microsoft-Windows-Kernel-WHEA/Errors Log\n[unavailable: The specified channel could not be found.]\n\n")
    queued = await hc.receive_device_result(
        await repo.get(started["id"]), await tray_repo.get_command(started["command_id"]), phase="snapshot",
        snapshot_text=json.dumps(WINDOWS_SNAPSHOT).encode(), log_bytes=_bundle(logs), error="",
    )
    assert queued and store["scheduled"] == [("triage", started["id"])]
    check = await repo.get(started["id"])
    assert check["status"] == "analysing" and check["snapshot"]["sections"]["volumes"][0]["drive"] == "C:"
    assert check["pawnio_status"] == hc.PAWNIO_LABELS["installed_and_removed"]
    assert {s["source"] for s in check["logs"]["sources"]} == {"System", "Microsoft-Windows-Kernel-WHEA/Errors"}
    assert (await tray_repo.get_command(started["command_id"]))["status"] == "completed"
    entries = await required_repo.list_entries()
    sources = {(e["source"], e["reason_code"]) for e in entries}
    # Unavailable on the endpoint, and requested but never collected (an older agent skipped them).
    assert ("Microsoft-Windows-Kernel-WHEA/Errors", "unavailable") in sources
    assert ("Microsoft-Windows-Ntfs/Operational", "unavailable") in sources
    assert all(e["features"] == "health_check" and e["last_health_check_id"] == started["id"] for e in entries)

    # Triage asks for one more allowed log and one that is not on the approved list.
    chats = []

    async def chat(llm, messages):
        chats.append(messages)
        if len(chats) == 1:
            return json.dumps({
                "issues": [{"area": "drives", "title": "HDD failing", "severity": "critical", "evidence": "Predictive Failure"}],
                "search_terms": ["hard drive predictive failure Windows 11"],
                "log_requests": [
                    {"source": "file:%SystemRoot%\\Logs\\CBS\\CBS.log", "reason": "Servicing errors", "hours": 48},
                    {"source": "System", "reason": "already collected"},
                    {"source": "Microsoft-Windows-Kernel-EventTracing/Admin", "reason": "ETW sessions failing"},
                ],
            })
        return json.dumps({
            "overall_status": "attention", "summary": "The old HDD is failing; back it up and replace it.",
            "areas": [{"area": "storage", "status": "critical", "summary": "HDD failing"}],
            "findings": [{"title": "HDD failing", "severity": "critical", "area": "storage", "evidence": "x", "source": "disks"}],
            "steps": [{"action": "Back up the data", "detail": "Copy D: now", "priority": "now", "area": "storage",
                       "reference": "Replace a failing drive"}],
            "required_logs": [{"source": "file:C:\\Vendor\\app\\trace.log", "reason": "vendor trace"}],
        })

    monkeypatch.setattr(hc, "_chat", chat)
    await hc.run_triage(started["id"])
    system_prompt = chats[0][0]["content"]
    assert "ALREADY_COLLECTED:\n- System" in system_prompt and "ALLOWED_LOG_SOURCES" in system_prompt
    assert "Microsoft-Windows-Kernel-WHEA/Errors" in system_prompt
    check = await repo.get(started["id"])
    assert check["status"] == "collecting" and check["command_id"] != started["command_id"]
    uid, logs_payload = store["sent"][-1]
    assert logs_payload["phase"] == "logs" and logs_payload["allow_pawnio"] is False
    assert logs_payload["log_requests"] == [{"source": "file:C:\\Windows\\Logs\\CBS\\CBS.log", "reason": "Servicing errors", "hours": 48}]
    refused = await required_repo.get_by_key(
        ai_required_logs.source_key("windows", "not_allowed", "Microsoft-Windows-Kernel-EventTracing/Admin"))
    assert refused["approve_kind"] == "event_log" and refused["approve_value"] == "Microsoft-Windows-Kernel-EventTracing/Admin"

    # The extra logs arrive and the report is written.
    store["scheduled"].clear()
    queued = await hc.receive_device_result(
        check, await tray_repo.get_command(check["command_id"]), phase="logs", snapshot_text=None,
        log_bytes=_bundle("### file:C:\\Windows\\Logs\\CBS\\CBS.log Log\n2026-10-22 Error 0x800f081f\n\n"), error="",
    )
    assert queued and store["scheduled"] == [("report", started["id"])]
    await hc.run_report(started["id"])
    check = await repo.get(started["id"])
    assert check["status"] == "completed" and check["completed_at"]
    # The rules found critical problems, so the model's milder status is overruled.
    assert check["overall_status"] == "critical" and check["health_score"] == 0
    report = check["report"]
    assert report["steps"][0]["action"] == "Back up the data" and report["triage"]["issues"][0]["area"] == "storage"
    assert report["required_logs"]["not_allowed"][0]["source"] == "Microsoft-Windows-Kernel-EventTracing/Admin"
    assert {s["phase"] for s in check["logs"]["sources"]} == {"snapshot", "logs"}
    vendor = await required_repo.get_by_key(ai_required_logs.source_key("windows", "not_allowed", "file:C:\\Vendor\\app\\trace.log"))
    assert vendor["approve_kind"] == "folder" and vendor["approve_value"] == "C:\\Vendor\\app"


@pytest.mark.anyio
async def test_report_without_the_model_still_uses_the_rules(store, monkeypatch):
    check_id = await repo.create(company_id=1, asset_id=10, tray_device_id=5, ticket_id=77, platform="windows",
                                 model="m", allow_pawnio=False, requested_by_user_id=None)
    await repo.update(check_id, status="analysing", snapshot=WINDOWS_SNAPSHOT, logs={"sources": []})

    async def failing_chat(llm, messages):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(hc, "_chat", failing_chat)
    await hc.run_triage(check_id)
    check = await repo.get(check_id)
    assert check["status"] == "completed" and check["overall_status"] == "critical"
    assert check["report"]["ai_error"] == "connection refused" and check["report"]["signals"]
    assert check["report"]["triage"]["error"] == "connection refused"
    # The ticket gets the outcome and a link to the report.
    ticket_id, note = store["notes"][-1]
    assert ticket_id == 77 and f'href="/health-checks/{check_id}"' in note and "automatic checks only" in note


@pytest.mark.anyio
async def test_start_refuses_bad_devices_and_respects_the_pawnio_setting(store, monkeypatch):
    asset = {"id": 10, "company_id": 1, "name": "PC-01"}
    with pytest.raises(hc.HealthCheckError, match="different company"):
        await hc.start_health_check(asset=asset, device={**await _device(5), "company_id": 2}, requested_by=None)
    with pytest.raises(hc.HealthCheckError, match="not active"):
        await hc.start_health_check(asset=asset, device={**await _device(5), "status": "revoked"}, requested_by=None)
    monkeypatch.setattr(hc.get_settings(), "health_check_pawnio_enabled", False)
    started = await hc.start_health_check(asset=asset, device=await _device(5), requested_by=None, allow_pawnio=True)
    assert started["allow_pawnio"] is False and store["sent"][-1][1]["allow_pawnio"] is False


@pytest.mark.anyio
async def test_start_needs_a_deployed_rmm_agent(store):
    asset = {"id": 10, "company_id": 1, "name": "PC-01"}
    store["agents"].pop(10)
    with pytest.raises(hc.HealthCheckError, match="Deploy the RMM agent"):
        await hc.start_health_check(asset=asset, device=await _device(5), requested_by=None)
    # An agent registered to another company does not count.
    store["agents"][10] = {"id": 3, "asset_id": 10, "company_id": 2}
    with pytest.raises(hc.HealthCheckError, match="Deploy the RMM agent"):
        await hc.start_health_check(asset=asset, device=await _device(5), requested_by=None)
    assert store["sent"] == [] and await repo.list_checks(asset_id=10) == []


@pytest.mark.anyio
async def test_offline_device_queues_and_stale_checks_expire(store, monkeypatch):
    async def offline(device_uid, payload):
        return False

    monkeypatch.setattr(tray_service, "send_to_device", offline)
    started = await hc.start_health_check(asset={"id": 10, "company_id": 1, "name": "PC-01"},
                                          device=await _device(5), requested_by=None)
    assert started["status"] == "queued" and not started["delivered"]
    old = (datetime.now(timezone.utc) - timedelta(days=2)).replace(tzinfo=None)
    await store["db"].execute("UPDATE ai_health_checks SET updated_at = %s WHERE id = %s", (old, started["id"]))
    assert await hc.expire_stale() == 1
    check = await repo.get(started["id"])
    assert check["status"] == "failed" and "did not pick up" in check["error"]
    command = await tray_repo.get_command(started["command_id"])
    assert command["status"] == "error"  # never delivered later

    # A check waiting for extra logs goes on to the report with what it has.
    check_id = await repo.create(company_id=1, asset_id=11, tray_device_id=6, ticket_id=None, platform="windows",
                                 model="m", allow_pawnio=False, requested_by_user_id=None)
    await repo.update(check_id, status="collecting", snapshot=WINDOWS_SNAPSHOT)
    await store["db"].execute("UPDATE ai_health_checks SET updated_at = %s WHERE id = %s",
                              ((datetime.now(timezone.utc) - timedelta(hours=3)).replace(tzinfo=None), check_id))
    await hc.expire_stale()
    assert (await repo.get(check_id))["status"] == "analysing" and ("report", check_id) in store["scheduled"]


def test_only_tray_apps_that_handle_the_command_get_it():
    assert hc.tray_can_run({"agent_version": None})  # not reported yet: benefit of the doubt
    assert hc.tray_can_run({"agent_version": "v0.9.10"})
    assert hc.tray_can_run({"agent_version": "0.9.10-3-gabc123"})
    assert hc.tray_can_run({"agent_version": "v0.10.0"})
    assert not hc.tray_can_run({"agent_version": "v0.9.9"})
    assert not hc.tray_can_run({"agent_version": "v0.9.5.3"})
    assert not hc.tray_can_run({"agent_version": "0.0.0-dev"})


@pytest.mark.anyio
async def test_a_check_started_on_an_old_tray_runs_once_the_tray_app_updates(store):
    await store["db"].execute("UPDATE tray_devices SET agent_version = 'v0.9.9' WHERE id = 5")
    device = await _device(5)
    started = await hc.start_health_check(asset={"id": 10, "company_id": 1, "name": "PC-01"},
                                          device=device, requested_by={"id": 9})
    assert started["status"] == "queued" and not started["delivered"]
    # Only the tray app's own update goes out; the old tray app would ignore the check.
    assert [payload["type"] for _uid, payload in store["sent"]] == ["update"]
    check = await repo.get(started["id"])
    assert "too old" in check["stage_message"] and "0.9.10" in check["stage_message"]
    assert (await tray_repo.get_command(started["command_id"]))["status"] == "queued"

    # Reconnecting before the update: the check is held back.
    store["sent"].clear()
    await tray_service.deliver_queued_commands(device)
    assert store["sent"] == []
    # The updated tray app reports its version: the check goes out (the drain
    # reads the version fresh, not from the device it was handed at connect).
    await store["db"].execute("UPDATE tray_devices SET agent_version = 'v0.9.10' WHERE id = 5")
    await tray_service.deliver_queued_commands(device)
    assert [(uid, payload["type"], payload["command_id"], payload["health_check_id"])
            for uid, payload in store["sent"]] == [("dev-1", "health_check", started["command_id"], started["id"])]
    assert (await tray_repo.get_command(started["command_id"]))["status"] == "delivered"


@pytest.mark.anyio
async def test_a_failed_logs_phase_still_writes_the_report(store):
    check_id = await repo.create(company_id=1, asset_id=10, tray_device_id=5, ticket_id=None, platform="windows",
                                 model="m", allow_pawnio=False, requested_by_user_id=None)
    command_id = await tray_repo.log_command(device_id=5, command="health_check", initiated_by_user_id=None,
                                             payload_json=json.dumps({"health_check_id": check_id, "phase": "logs"}))
    await repo.update(check_id, status="collecting", snapshot=WINDOWS_SNAPSHOT, command_id=command_id)
    await hc.record_device_status(await repo.get(check_id), await tray_repo.get_command(command_id), "failed",
                                  "Another health check is already running on this device.")
    check = await repo.get(check_id)
    assert check["status"] == "analysing" and "extra logs could not be collected" in check["error"]
    assert ("report", check_id) in store["scheduled"]
    assert (await tray_repo.get_command(command_id))["status"] == "error"


@pytest.mark.anyio
async def test_device_failure_and_empty_results(store):
    started = await hc.start_health_check(asset={"id": 10, "company_id": 1, "name": "PC-01"},
                                          device=await _device(5), requested_by=None)
    check = await repo.get(started["id"])
    command = await tray_repo.get_command(started["command_id"])
    queued = await hc.receive_device_result(check, command, phase="snapshot", snapshot_text=b"", log_bytes=b"",
                                            error="powershell.exe not found")
    assert not queued
    check = await repo.get(started["id"])
    assert check["status"] == "failed" and check["error"] == "powershell.exe not found"

    # A failure before any snapshot fails the whole check.
    started = await hc.start_health_check(asset={"id": 11, "company_id": 1, "name": "PC-02"},
                                          device=await _device(6), requested_by=None)
    await hc.record_device_status(await repo.get(started["id"]), await tray_repo.get_command(started["command_id"]),
                                  "failed", "PowerShell is blocked")
    check = await repo.get(started["id"])
    assert check["status"] == "failed" and check["error"] == "PowerShell is blocked"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(store, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, RedirectResponse
    from fastapi.testclient import TestClient

    from app.api.dependencies import auth
    from app.features.health_check import routes

    state = {"device": None, "user": {"id": 9, "is_super_admin": True}, "technician": True,
             "rmm_read": True, "rmm_write": True, "rmm_pack": True, "company_id": 1}

    async def current_device():
        return state["device"]

    async def current_user():
        return state["user"]

    async def require_authenticated_user(request):
        return state["user"], None

    async def is_technician(user, request=None):
        return state["technician"]

    async def load_asset_context(request, permission_key="menu.assets", *, asset_id=None):
        assert permission_key == "menu.rmm_scripts"
        if not (state["user"].get("is_super_admin") or state["rmm_read"]):
            return state["user"], {}, None, state["company_id"], RedirectResponse("/", status_code=303)
        return state["user"], {}, None, state["company_id"], None

    def menu_can(user, membership, key, write=False):
        return state["rmm_write"] if write else state["rmm_read"]

    async def require_super_admin_page(request):
        return state["user"], None

    rendered = []

    async def render_template(template, request, user, extra=None):
        rendered.append((template, extra))
        return HTMLResponse(template)

    main = SimpleNamespace(_require_authenticated_user=require_authenticated_user, _is_helpdesk_technician=is_technician,
                           _require_super_admin_page=require_super_admin_page, _render_template=render_template,
                           _feature_pack_available=lambda slug: state["rmm_pack"] if slug == "rmm" else True,
                           _membership_menu_can=menu_can)
    monkeypatch.setattr(routes, "_main", lambda: main)
    from app.features.assets import routes as asset_routes

    monkeypatch.setattr(asset_routes, "_load_asset_context", load_asset_context)
    audits = []

    async def log_action(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(routes.audit_service, "log_action", log_action)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[auth.get_current_tray_device] = current_device
    app.dependency_overrides[auth.require_helpdesk_technician] = current_user
    app.dependency_overrides[auth.require_super_admin] = current_user
    with TestClient(app) as test_client:
        yield test_client, state, rendered, audits


async def _asset_lookup(asset_id):
    return {10: {"id": 10, "company_id": 1, "name": "PC-01", "os_name": "Windows 11"}}.get(asset_id)


@pytest.mark.anyio
async def test_device_callbacks_check_the_device_command_and_phase(client, store, monkeypatch):
    test_client, state, _rendered, audits = client
    monkeypatch.setattr("app.features.health_check.routes.asset_repo.get_asset_by_id", _asset_lookup)
    response = test_client.post("/api/assets/10/health-checks", json={"allow_pawnio": False})
    assert response.status_code == 202, response.text
    started = response.json()
    assert started["url"] == f"/health-checks/{started['id']}" and audits[-1]["action"] == "health_check.start"
    check_id, command_id = started["id"], started["command_id"]

    state["device"] = await _device(6)  # another device in the same company
    form = {"command_id": str(command_id), "stage": "collecting_snapshot", "message": "x"}
    assert test_client.post(f"/api/health-checks/{check_id}/device-status", data=form).status_code == 403
    state["device"] = await _device(5)
    assert test_client.post(f"/api/health-checks/{check_id}/device-status", data={**form, "stage": "pwn"}).status_code == 400
    assert test_client.post(f"/api/health-checks/{check_id + 1}/device-status", data=form).status_code == 400
    assert test_client.post(f"/api/health-checks/{check_id}/device-status", data=form).status_code == 200

    result = {"command_id": str(command_id), "phase": "logs"}
    assert test_client.post(f"/api/health-checks/{check_id}/device-result", data=result).status_code == 400
    files = {"snapshot": ("s.json", json.dumps(WINDOWS_SNAPSHOT).encode(), "application/json")}
    response = test_client.post(f"/api/health-checks/{check_id}/device-result",
                                data={**result, "phase": "snapshot"}, files=files)
    assert response.status_code == 200 and response.json()["analysis_queued"] is True
    # The command has finished, so a replay is refused.
    replay = test_client.post(f"/api/health-checks/{check_id}/device-result",
                              data={**result, "phase": "snapshot"}, files=files)
    assert replay.status_code == 409

    detail = test_client.get(f"/api/health-checks/{check_id}").json()["health_check"]
    assert detail["status"] == "analysing" and detail["snapshot"]["sections"]["volumes"][0]["drive"] == "C:"


@pytest.mark.anyio
async def test_start_errors_are_reported(client, store, monkeypatch):
    test_client, state, _rendered, _audits = client
    monkeypatch.setattr("app.features.health_check.routes.asset_repo.get_asset_by_id", _asset_lookup)
    assert test_client.post("/api/assets/99/health-checks", json={}).status_code == 404
    first = test_client.post("/api/assets/10/health-checks", json={})
    assert first.status_code == 202
    again = test_client.post("/api/assets/10/health-checks", json={})
    assert again.status_code == 409 and "already running" in again.json()["detail"]


@pytest.mark.anyio
async def test_asset_actions_menu_route_is_an_rmm_option(client, store, monkeypatch):
    test_client, state, _rendered, audits = client
    monkeypatch.setattr("app.features.health_check.routes.asset_repo.get_asset_by_id", _asset_lookup)
    monkeypatch.setattr(hc.get_settings(), "health_check_pawnio_enabled", True)

    # The Actions menu sends no PawnIO choice: the setting decides.
    response = test_client.post("/rmm/assets/10/health-check", follow_redirects=False)
    assert response.status_code == 303, response.text
    check = (await repo.list_checks(asset_id=10))[0]
    assert response.headers["location"] == f"/health-checks/{check['id']}"
    assert store["sent"][-1][1]["allow_pawnio"] is True and audits[-1]["action"] == "health_check.start"

    # The section's form sends its checkbox; unticked means no PawnIO.
    await repo.update(check["id"], status="completed", completed=True)
    response = test_client.post("/rmm/assets/10/health-check", data={"pawnio_choice": "1"}, follow_redirects=False)
    assert response.status_code == 303 and store["sent"][-1][1]["allow_pawnio"] is False
    await repo.update((await repo.list_checks(asset_id=10))[0]["id"], status="completed", completed=True)

    # Without a deployed RMM agent the asset page gets an error instead.
    agent = store["agents"].pop(10)
    response = test_client.post("/rmm/assets/10/health-check", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/assets/10#health-check"
    assert len(await repo.list_checks(asset_id=10)) == 2
    api = test_client.post("/api/assets/10/health-checks", json={})
    assert api.status_code == 409 and "RMM agent" in api.json()["detail"]
    store["agents"][10] = agent

    # RMM permissions, like Check in now.
    state["user"] = {"id": 12, "is_super_admin": False}
    state["rmm_write"] = False
    assert test_client.post("/rmm/assets/10/health-check", follow_redirects=False).status_code == 403
    state["rmm_write"] = True
    state["company_id"] = 2
    assert test_client.post("/rmm/assets/10/health-check", follow_redirects=False).status_code == 404
    state["company_id"] = 1
    state["rmm_pack"] = False
    assert test_client.post("/rmm/assets/10/health-check", follow_redirects=False).status_code == 404


@pytest.mark.anyio
async def test_rmm_users_see_only_their_companys_checks(client, store, monkeypatch):
    test_client, state, rendered, _audits = client
    mine = await repo.create(company_id=1, asset_id=10, tray_device_id=5, ticket_id=None, platform="windows",
                             model="m", allow_pawnio=False, requested_by_user_id=None)
    theirs = await repo.create(company_id=2, asset_id=20, tray_device_id=7, ticket_id=None, platform="windows",
                               model="m", allow_pawnio=False, requested_by_user_id=None)
    state.update(user={"id": 12, "is_super_admin": False}, technician=False, rmm_write=False)
    assert test_client.get("/health-checks").status_code == 200
    assert [check["id"] for check in rendered[-1][1]["checks"]] == [mine]
    assert test_client.get(f"/health-checks/{mine}").status_code == 200
    assert rendered[-1][1]["can_run_again"] is False  # read-only RMM access
    assert test_client.get(f"/health-checks/{theirs}").status_code == 404
    state["rmm_read"] = False
    assert test_client.get("/health-checks").status_code == 403


@pytest.mark.anyio
async def test_pages_render(client, store, monkeypatch):
    test_client, state, rendered, _audits = client
    monkeypatch.setattr("app.features.health_check.routes.asset_repo.get_asset_by_id", _asset_lookup)
    started = test_client.post("/api/assets/10/health-checks", json={}).json()
    assert test_client.get("/health-checks").status_code == 200
    template, extra = rendered[-1]
    assert template == "health_checks/index.html" and extra["checks"][0]["id"] == started["id"] and extra["running"] == 1
    assert test_client.get(f"/health-checks/{started['id']}").status_code == 200
    template, extra = rendered[-1]
    assert template == "health_checks/report.html" and extra["check"]["is_active"] and extra["can_run_again"]
    assert test_client.get("/health-checks/999").status_code == 404


def _render(name, **context):
    env = _environment()
    env.globals["static_url"] = lambda path: path
    return env.get_template(name).render(csrf_token="tok", is_super_admin=True, **context)


def test_report_template_renders_and_escapes():
    signals = hc.evaluate_snapshot(WINDOWS_SNAPSHOT)
    check = hc.present({
        "id": 4, "asset_id": 10, "asset_name": "PC-<b>01</b>", "company_id": 1, "company_name": "Contoso",
        "status": "completed", "overall_status": "critical", "health_score": 12, "platform": "windows",
        "model": "m", "allow_pawnio": True, "pawnio_status": hc.PAWNIO_LABELS["installed_and_removed"],
        "ticket_id": 77, "created_at": "2026-10-22 01:00:00", "completed_at": "2026-10-22 01:09:00",
        "summary": "Back up now", "error": None, "stage_message": "Report ready.",
    })
    report = {
        "summary": "<script>alert(1)</script> Back up now", "signals": signals,
        "steps": [{"action": "Back up the data", "detail": "Copy D:", "priority": "now", "area": "storage",
                   "reference": "KB", "reference_url": ""}],
        "findings": [{"title": "HDD failing", "severity": "critical", "area": "storage", "evidence": "<pre>", "source": "disks"}],
        "areas": [{"area": "storage", "status": "critical", "summary": "Failing"}],
        "required_logs": {"not_allowed": [{"source": "Microsoft-Windows-Kernel-EventTracing/Admin", "reason": "ETW"}],
                          "not_collected": []},
        "kb_articles": [{"title": "Drive replacement", "slug": "drive-replacement"}],
        "web_pages": [{"title": "Docs", "url": "https://learn.microsoft.com/x"}],
    }
    from app.features.health_check.routes import _snapshot_view

    html = _render("health_checks/report.html", check=check, report=report, signals=signals, can_run_again=True,
                   snapshot=_snapshot_view(WINDOWS_SNAPSHOT),
                   logs=[{"source": "System", "reason": "disk", "text": "<b>bad</b>", "truncated": False,
                          "unavailable": None, "phase": "snapshot"}],
                   areas=hc.AREAS, previous=[])
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)" in html
    assert "&lt;b&gt;bad&lt;/b&gt;" in html and "&lt;pre&gt;" in html
    assert "Steps to optimal performance" in html and "Back up the data" in html
    assert "Old HDD" in html and "Predictive Failure" in html and "Hardware sensors: Temperature" in html
    assert 'href="/admin/required-logs"' in html and "Microsoft-Windows-Kernel-EventTracing/Admin" in html
    assert "[Run again]" in html and 'action="/rmm/assets/10/health-check"' in html
    assert 'name="pawnio_choice" value="1"' in html and 'name="allow_pawnio" value="1"' in html
    assert "PawnIO was installed for the check and removed afterwards." in html


def test_index_and_required_logs_templates_render():
    check = hc.present({"id": 4, "asset_id": 10, "asset_name": "PC-01", "company_name": "Contoso", "status": "collecting",
                        "created_at": "2026-10-22 01:00:00", "stage_message": "Reading hardware sensors"})
    html = _render("health_checks/index.html", checks=[check], filter_asset=None,
                   counts={"critical": 0, "attention": 0, "healthy": 0, "unknown": 0}, running=1,
                   status_labels=hc.STATUS_LABELS)
    assert 'href="/health-checks/4"' in html and "Reading hardware sensors" in html
    assert "No health checks yet" in _render("health_checks/index.html", checks=[], filter_asset=None,
                                             counts={}, running=0, status_labels=hc.STATUS_LABELS)


def test_asset_actions_menu_offers_the_health_check_only_with_an_rmm_agent():
    root = ROOT / "app/templates"
    source = (root / "assets/detail.html").read_text(encoding="utf-8")
    fragment = source.split("{% block header_actions %}", 1)[1].split("{% endblock %}", 1)[0]
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(root), autoescape=True)
    template = env.from_string('{% from "macros/header.html" import page_header_actions with context %}' + fragment)
    health = {"can_manage": True, "can_run": True, "active": False, "pawnio_available": True}
    values = {"asset": {"id": 10, "company_id": 1}, "rmm": {"can_run": True, "agent": {"id": 1}},
              "health_check": health, "can_edit": False, "customer_safe": False}
    html = template.render(**values)
    assert "Run health check" in html and 'form="asset-health-check-form"' in html and "PawnIO" in html
    # The asset page only builds the health check context when an RMM agent is deployed.
    assert "Run health check" not in template.render(**{**values, "health_check": None})
    assert "Run health check" not in template.render(**{**values, "customer_safe": True})
    assert "Run health check" not in template.render(**{**values, "health_check": {**health, "can_manage": False}})
    busy = template.render(**{**values, "health_check": {**health, "active": True}})
    assert "A health check is already running" in busy and "disabled" in busy.split("Run health check")[0].rsplit("<button", 1)[1]
    no_tray = template.render(**{**values, "health_check": {**health, "can_run": False}})
    assert "Needs the tray app connected" in no_tray
    form = source[source.index('<form id="asset-health-check-form"'):].split("</form>", 1)[0] + "</form>"
    rendered = env.from_string(form).render(asset={"id": 10}, csrf_token="test-csrf")
    assert 'method="post"' in rendered and "/rmm/assets/10/health-check" in rendered and "test-csrf" in rendered
