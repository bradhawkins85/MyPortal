# MyPortal printer scan (app.services.rmm_printers).
# Runs through the RMM agent's script runner as the service account, so no
# agent update is needed. Lists the device's printers with Get-Printer, adds
# each signed-in user's own printer connections and default printer from
# their loaded registry hive, and prints one ##myportal-printers= line of
# gzip-compressed, base64-encoded JSON.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$errors = New-Object System.Collections.Generic.List[string]
$printers = [ordered]@{}

function Add-MyPortalPrinter([hashtable]$Printer) {
    $key = ([string]$Printer.name).ToLowerInvariant()
    if (-not $key) { return }
    if (-not $printers.Contains($key)) { $printers[$key] = $Printer }
}

try {
    $ports = @{}
    try {
        Get-PrinterPort -ErrorAction Stop | ForEach-Object { $ports[$_.Name] = $_.PrinterHostAddress }
    } catch { $errors.Add("ports: $($_.Exception.Message)") }
    if (Get-Command Get-Printer -ErrorAction SilentlyContinue) {
        Get-Printer -ErrorAction Stop | ForEach-Object {
            Add-MyPortalPrinter @{
                name = $_.Name; driver = $_.DriverName; port = $_.PortName; port_address = $ports[$_.PortName]
                type = [string]$_.Type; device_type = [string]$_.DeviceType; status = [string]$_.PrinterStatus
                shared = [bool]$_.Shared; share_name = $_.ShareName; published = [bool]$_.Published
                location = $_.Location; comment = $_.Comment; computer_name = $_.ComputerName
                users = @(); default_for = @()
            }
        }
    } else {
        Get-CimInstance Win32_Printer | ForEach-Object {
            Add-MyPortalPrinter @{
                name = $_.Name; driver = $_.DriverName; port = $_.PortName; port_address = $ports[$_.PortName]
                type = $(if ($_.Network) { 'Connection' } else { 'Local' }); device_type = 'Print'
                status = [string]$_.PrinterStatus; shared = [bool]$_.Shared; share_name = $_.ShareName
                published = [bool]$_.Published; location = $_.Location; comment = $_.Comment
                computer_name = $_.ServerName; users = @(); default_for = @()
            }
        }
    }
} catch { $errors.Add("printers: $($_.Exception.Message)") }

# Per-user printer connections and default printers of signed-in users.
try {
    Get-ChildItem 'Registry::HKEY_USERS' -ErrorAction Stop |
        Where-Object { $_.PSChildName -match '^S-1-5-21-[\d-]+$' } | ForEach-Object {
            $sid = $_.PSChildName
            try {
                $user = (New-Object Security.Principal.SecurityIdentifier($sid)).Translate([Security.Principal.NTAccount]).Value
            } catch { $user = $sid }
            $hive = "Registry::HKEY_USERS\$sid"
            Get-ChildItem "$hive\Printers\Connections" -ErrorAction SilentlyContinue | ForEach-Object {
                # ",,server,printer" is \\server\printer
                $parts = $_.PSChildName -split ','
                if ($parts.Count -ge 4) {
                    $name = '\\' + $parts[2] + '\' + (($parts[3..($parts.Count - 1)]) -join ',')
                    $key = $name.ToLowerInvariant()
                    if (-not $printers.Contains($key)) {
                        $printers[$key] = @{ name = $name; type = 'Connection'; device_type = 'Print'
                                             computer_name = $parts[2]; users = @(); default_for = @() }
                    }
                    $printers[$key].users += $user
                }
            }
            $device = (Get-ItemProperty "$hive\Software\Microsoft\Windows NT\CurrentVersion\Windows" -Name Device -ErrorAction SilentlyContinue).Device
            if ($device) {
                $name = ($device -split ',')[0]
                $key = $name.ToLowerInvariant()
                if ($printers.Contains($key)) { $printers[$key].default_for += $user }
            }
        }
} catch { $errors.Add("users: $($_.Exception.Message)") }

$state = [ordered]@{
    scanned_at = (Get-Date).ToUniversalTime().ToString('o')
    printers   = @($printers.Values)
    errors     = @($errors)
}
$json = ConvertTo-Json -InputObject $state -Depth 5 -Compress
$bytes = [Text.Encoding]::UTF8.GetBytes($json)
$buffer = New-Object IO.MemoryStream
$gzip = New-Object IO.Compression.GZipStream($buffer, [IO.Compression.CompressionMode]::Compress)
$gzip.Write($bytes, 0, $bytes.Length)
$gzip.Dispose()
# Written directly to stdout so PowerShell's formatting never wraps the long line.
[Console]::Out.WriteLine('##myportal-printers=' + [Convert]::ToBase64String($buffer.ToArray()))
[Console]::Out.Flush()
Write-Output "Printers found: $($printers.Count)"
foreach ($problem in $errors) { Write-Output "Printer scan: $problem" }
if (-not $printers.Count -and $errors.Count) { exit 1 }
