from pathlib import Path


# These modules already wrap each attempt with Webhook Monitor manually.  The
# dispatcher itself must remain raw to prevent recursive monitor events.
MANUALLY_MONITORED = {
    "app/services/call_recordings.py",
    "app/services/modules.py",
    "app/services/solidtime.py",
    "app/services/syncro.py",
    "app/services/transcription.py",
    "app/services/webhook_monitor.py",
    "app/services/xero.py",
}


def test_new_httpx_clients_cannot_bypass_monitoring():
    offenders = []
    for path in Path("app").rglob("*.py"):
        relative = path.as_posix()
        if relative in MANUALLY_MONITORED or relative.endswith("monitored_http.py"):
            continue
        if "httpx.AsyncClient(" in path.read_text():
            offenders.append(relative)
    assert offenders == [], "Use MonitoredAsyncClient for outbound HTTP: " + ", ".join(offenders)
