import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tray" / "build-agent"


def _script(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _function(script: str, name: str) -> str:
    return script.split(f"function {name}", 1)[1].split("\n}\n", 1)[0]


def test_cleanup_runs_between_builds_and_cannot_stop_the_agent():
    agent = _script("MyPortalBuildAgent.ps1")
    main = agent.split("$mutex = [System.Threading.Mutex]", 1)[1]

    # Runs inside the mutex, before claiming work, and its failures are logged.
    assert main.index("Invoke-Cleanup") < main.index("Invoke-Queue")
    assert 'Write-Log "Cleanup failed: $($_.Exception.Message)"' in main
    assert "$CleanupIntervalHours = 6" in agent
    cleanup = _function(agent, "Invoke-Cleanup")
    assert cleanup.index("Set-Content -LiteralPath $stamp") < cleanup.index("Clear-OldPayloads")


def test_payloads_keep_the_most_recently_used_releases():
    agent = _script("MyPortalBuildAgent.ps1")
    payload = _function(agent, "Get-Payload")
    assert "SetLastWriteTimeUtc($marker, [DateTime]::UtcNow)" in payload

    prune = _function(agent, "Clear-OldPayloads")
    assert "Sort-Object Used -Descending | Select-Object -Skip $KeepPayloads" in prune
    assert "Removing unfinished payload" in prune
    assert "if ($KeepPayloads -lt 1) { $KeepPayloads = 1 }" in agent


def test_temp_cleanup_is_limited_to_old_unlinked_items_in_temp_folders():
    agent = _script("MyPortalBuildAgent.ps1")

    folders = _function(agent, "Get-TempFolders")
    assert "Join-Path $env:SystemRoot 'SystemTemp'" in folders
    leaf = re.search(r"-match '(\^\([^']+\)\$)'", folders).group(1)
    allowed = re.compile(leaf, re.IGNORECASE)
    assert allowed.match("SystemTemp") and allowed.match("Temp")
    assert not allowed.match("Windows") and not allowed.match("System32")

    cleanup = _function(agent, "Invoke-Cleanup")
    assert "if ($TempRetentionDays -gt 0)" in cleanup
    assert "(Get-LatestWriteTime $item) -lt $cutoff" in cleanup

    remove = _function(agent, "Remove-StaleItem")
    assert "ReparsePoint) { return $freed }" in remove
    code = "\n".join(line for line in remove.splitlines() if not line.strip().startswith("#"))
    assert "-FollowSymlink" not in code
    assert "Remove-Item" not in code


def test_installer_writes_cleanup_settings():
    installer = _script("Install-MyPortalBuildAgent.ps1")
    assert "[ValidateRange(1, 50)][int]$KeepPayloads = 3" in installer
    assert "[ValidateRange(0, 365)][int]$TempRetentionDays = 3" in installer
    assert "KeepPayloads          = $KeepPayloads" in installer
    assert "TempRetentionDays     = $TempRetentionDays" in installer
