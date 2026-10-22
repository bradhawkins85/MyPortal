# Data is JSON; source definitions are never evaluated as PowerShell.
function Find-ChocolateyExecutable {
    $command = Get-Command choco.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    # A running agent does not inherit PATH or ChocolateyInstall changes made
    # by an installer. Check both custom and default installation locations.
    $roots = @($env:ChocolateyInstall, [Environment]::GetEnvironmentVariable('ChocolateyInstall', 'Machine'))
    if ($env:ProgramData) { $roots += Join-Path $env:ProgramData 'chocolatey' }
    foreach ($root in $roots) {
        if (-not $root) { continue }
        $candidate = Join-Path $root 'bin\choco.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
}

function Ensure-ChocolateyInstalled {
    $script:choco = Find-ChocolateyExecutable
    if (-not $script:choco) {
        Write-Output 'Chocolatey is missing; installing Chocolatey.'
        # Isolate the official installer so its exit/scope changes cannot end
        # this job before company sources have been reconciled. No policy data
        # or source credentials are interpolated into this command.
        $bootstrap = @'
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
Invoke-Expression ((New-Object Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
'@
        try {
            $null = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command $bootstrap 2>&1
            if ($LASTEXITCODE -ne 0) { throw 'Installer failed.' }
        } catch { throw 'Chocolatey installation failed.' }
        $script:choco = Find-ChocolateyExecutable
        if (-not $script:choco) { throw 'Chocolatey installation failed: choco.exe was not found after installation.' }
    }
    # Package scripts also need the shim directory in this job's inherited PATH.
    $env:Path = "$(Split-Path -Parent $script:choco);$env:Path"
}

function Sync-ChocolateySources($sources) {
    # Windows PowerShell 5.1 emits a JSON array from ConvertFrom-Json as one
    # object, so @(... | ConvertFrom-Json) is a single nested array. Flatten it
    # or source names collapse into one string and every source is disabled.
    $sources = @($sources | ForEach-Object { $_ })
    Ensure-ChocolateyInstalled
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
