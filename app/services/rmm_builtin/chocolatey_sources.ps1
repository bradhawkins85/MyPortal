# Data is JSON; source definitions are never evaluated as PowerShell.
function Sync-ChocolateySources($sources) {
    # Windows PowerShell 5.1 emits a JSON array from ConvertFrom-Json as one
    # object, so @(... | ConvertFrom-Json) is a single nested array. Flatten it
    # or source names collapse into one string and every source is disabled.
    $sources = @($sources | ForEach-Object { $_ })
    $command = Get-Command choco.exe -ErrorAction SilentlyContinue
    if ($command) { $script:choco = $command.Source }
    else {
        $script:choco = Join-Path $env:ProgramData 'chocolatey\bin\choco.exe'
        if (-not (Test-Path $script:choco)) { throw 'Chocolatey is not installed.' }
    }
    function Source-Command([string[]]$arguments) {
        # Suppress output: Chocolatey can echo credentials in its diagnostics.
        $null = & $script:choco @arguments 2>&1
        if ($LASTEXITCODE -ne 0) { throw 'Chocolatey source reconciliation failed.' }
    }
    $rows = @(& $script:choco source list --limit-output 2>&1)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot list Chocolatey sources.' }
    $existing = @($rows | ForEach-Object { ([string]$_).Split('|')[0] })
    $wanted = @($sources | Where-Object { $_.enabled } | ForEach-Object { [string]$_.name })
    foreach ($row in $rows) {
        $parts = ([string]$row).Split('|')
        if ($parts.Count -lt 2) { continue }
        $name = $parts[0]
        if ($wanted -notcontains $name) {
            Source-Command @('source', 'disable', "--name=$name", '--yes', '--limit-output')
        }
    }
    foreach ($source in $sources) {
        if (-not $source.enabled) { continue }
        if ($source.name -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$' -or $source.url -notmatch '^https?://' -or [int]$source.priority -lt 1) {
            throw 'Invalid Chocolatey source configuration.'
        }
        # Re-create an existing enabled source so removed/rotated credentials
        # cannot survive Chocolatey's update semantics.
        if ($existing -contains $source.name) {
            Source-Command @('source', 'remove', "--name=$($source.name)", '--yes', '--limit-output')
        }
        $arguments = @('source', 'add', "--name=$($source.name)", "--source=$($source.url)", "--priority=$($source.priority)", '--yes', '--limit-output')
        if ($source.username) { $arguments += @("--user=$($source.username)", "--password=$($source.password)") }
        Source-Command $arguments
        Source-Command @('source', 'enable', "--name=$($source.name)", '--yes', '--limit-output')
    }
    $actual = @(& $script:choco source list --limit-output 2>&1)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot verify Chocolatey sources.' }
    $enabledNames = @()
    foreach ($row in $actual) {
        $parts = ([string]$row).Split('|')
        # name|url|disabled|user|certificate|priority|bypassProxy|selfService|adminOnly
        if ($parts.Count -lt 6) { throw 'Cannot parse Chocolatey source state.' }
        if ($parts[2] -ieq 'false') {
            $expected = @($sources | Where-Object { $_.enabled -and $_.name -ieq $parts[0] -and $_.url -eq $parts[1] -and [int]$_.priority -eq [int]$parts[5] })
            if ($expected.Count -ne 1) { throw 'Unexpected enabled Chocolatey source.' }
            $enabledNames += $parts[0]
        }
    }
    if ($enabledNames.Count -ne $wanted.Count) { throw 'Chocolatey sources did not match company policy.' }
}
