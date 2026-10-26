# MyPortal health check: drive SMART data through CrystalDiskInfo. Runs as
# SYSTEM through the tray service and prints one JSON object:
# {"status", "installed", "detail", "text"}, where "text" is CrystalDiskInfo's
# own report (DiskInfo.txt), parsed by the tray service.
#
# Windows' reliability counters miss the wear and health figures many drives
# keep in vendor-specific SMART attributes (older SSDs often report 0% wear),
# so CrystalDiskInfo reads the attributes itself. When it is not installed it
# is installed with Chocolatey (and Chocolatey first, when that is missing);
# it stays installed afterwards so later checks do not install it again.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

$result = [ordered]@{ status = 'unavailable'; installed = $false; detail = $null; text = $null }

function Short($Text, [int]$Max = 300) {
    if ($null -eq $Text) { return $null }
    $value = ([string]$Text -replace '\s+', ' ').Trim()
    if ($value.Length -gt $Max) { return $value.Substring(0, $Max) }
    return $value
}

function Find-ChocolateyExecutable {
    $command = Get-Command choco.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    # The service does not inherit PATH or ChocolateyInstall changes made by
    # an installer, so check the custom and default install locations too.
    $roots = @($env:ChocolateyInstall, [Environment]::GetEnvironmentVariable('ChocolateyInstall', 'Machine'))
    if ($env:ProgramData) { $roots += Join-Path $env:ProgramData 'chocolatey' }
    foreach ($root in $roots) {
        if (-not $root) { continue }
        $candidate = Join-Path $root 'bin\choco.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
}

function Find-DiskInfo {
    $names = @('DiskInfo64.exe', 'DiskInfo32.exe')
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { $names = @('DiskInfoA64.exe') + $names }
    elseif (-not [Environment]::Is64BitOperatingSystem) { $names = @('DiskInfo32.exe') }
    $folders = @()
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($root) { $folders += Join-Path $root 'CrystalDiskInfo' }
    }
    foreach ($view in @('SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')) {
        Get-ChildItem -Path "HKLM:\$view" -ErrorAction SilentlyContinue | ForEach-Object {
            $entry = Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction SilentlyContinue
            if ($entry.DisplayName -like 'CrystalDiskInfo*' -and $entry.InstallLocation) { $folders += [string]$entry.InstallLocation }
        }
    }
    $choco = Find-ChocolateyExecutable
    if ($choco) {
        $chocoRoot = Split-Path -Parent (Split-Path -Parent $choco)
        $folders += Join-Path $chocoRoot 'lib\crystaldiskinfo.portable\tools'
        $folders += Join-Path $chocoRoot 'lib\crystaldiskinfo\tools'
    }
    foreach ($folder in $folders) {
        foreach ($name in $names) {
            $candidate = Join-Path $folder $name
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
    }
}

# Windows PowerShell turns a native command's stderr into a terminating error
# under 'Stop' when it is redirected, so the installers run under 'Continue'
# (set in each function's own scope) and are judged by their exit codes.
function Install-Chocolatey {
    $ErrorActionPreference = 'Continue'
    # The official installer runs in its own process so its exit or scope
    # changes cannot end this script.
    $bootstrap = @'
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
Invoke-Expression ((New-Object Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
'@
    $null = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command $bootstrap 2>&1
    $choco = Find-ChocolateyExecutable
    if (-not $choco) { throw 'Chocolatey could not be installed.' }
    return $choco
}

function Install-CrystalDiskInfo {
    $ErrorActionPreference = 'Continue'
    $choco = Find-ChocolateyExecutable
    if (-not $choco) { $choco = Install-Chocolatey }
    $env:Path = "$(Split-Path -Parent $choco);$env:Path"
    $output = @(& $choco install crystaldiskinfo --yes --no-progress --limit-output 2>&1)
    $code = $LASTEXITCODE
    # 3010 and 1641 mean installed, restart required.
    if ($code -notin @(0, 1641, 3010)) {
        throw "Chocolatey could not install CrystalDiskInfo (exit code $code): $(Short (($output | Select-Object -Last 3) -join ' ') 200)"
    }
}

# CrystalDiskInfo writes its report in the language of its own settings, so
# English is chosen before it runs; the parser reads the English labels.
function Set-EnglishLanguage([string]$Folder) {
    try {
        if (-not ('MyPortal.IniFile' -as [type])) {
            Add-Type -Namespace MyPortal -Name IniFile -MemberDefinition @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool WritePrivateProfileString(string section, string key, string value, string path);
'@
        }
        $null = [MyPortal.IniFile]::WritePrivateProfileString('Setting', 'Language', 'English', (Join-Path $Folder 'DiskInfo.ini'))
    } catch { }
}

try {
    $exe = Find-DiskInfo
    if (-not $exe) {
        Install-CrystalDiskInfo
        $exe = Find-DiskInfo
        if (-not $exe) { throw 'CrystalDiskInfo was installed but DiskInfo64.exe was not found.' }
        $result.installed = $true
    }
    $folder = Split-Path -Parent $exe
    Set-EnglishLanguage $folder
    $report = Join-Path $folder 'DiskInfo.txt'
    Remove-Item -LiteralPath $report -Force -ErrorAction SilentlyContinue
    $process = Start-Process -FilePath $exe -ArgumentList '/CopyExit' -WorkingDirectory $folder -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(180000)) {
        try { $process.Kill() } catch { }
        throw 'CrystalDiskInfo did not finish within 3 minutes.'
    }
    if (-not (Test-Path -LiteralPath $report -PathType Leaf)) { throw 'CrystalDiskInfo did not write DiskInfo.txt.' }
    # Get-Content detects the report's encoding (UTF-16 in recent versions) from its byte order mark.
    $text = (Get-Content -LiteralPath $report -Raw)
    if ($text.Length -gt 1500000) { $text = $text.Substring(0, 1500000) }
    $result.text = $text
    $result.status = 'available'
} catch {
    $result.status = 'error'
    $result.detail = Short ($_.Exception.Message) 300
}

ConvertTo-Json -InputObject $result -Compress
