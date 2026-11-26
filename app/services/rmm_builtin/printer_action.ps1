# MyPortal printer rename / remove (app.services.rmm_printers).
# Runs through the RMM agent's script runner as the service account. The
# request arrives as JSON in MYPORTAL_PRINTER_ACTION:
#   {"action": "rename" | "remove", "name": "<printer>", "new_name": "<new name>"}
# On success it prints one ##myportal-printer-action= line of JSON so MyPortal
# can update its printer list without waiting for the next scan.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-MyPortalResult([string]$Status, [string]$Message) {
    $result = [ordered]@{ status = $Status; message = $Message }
    [Console]::Out.WriteLine('##myportal-printer-action=' + (ConvertTo-Json -InputObject $result -Compress))
    [Console]::Out.Flush()
    Write-Output $Message
}

$request = $env:MYPORTAL_PRINTER_ACTION | ConvertFrom-Json
$action = [string]$request.action
$name = [string]$request.name
if (-not $name) { Write-Error 'No printer name was given.'; exit 2 }

function Get-MyPortalPrinter([string]$PrinterName) {
    if (Get-Command Get-Printer -ErrorAction SilentlyContinue) {
        return Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue
    }
    $escaped = $PrinterName.Replace('\', '\\').Replace("'", "\'")
    return Get-CimInstance Win32_Printer -Filter "Name = '$escaped'" -ErrorAction SilentlyContinue
}

if ($action -eq 'rename') {
    $newName = [string]$request.new_name
    if (-not $newName) { Write-Error 'No new name was given.'; exit 2 }
    if (-not (Get-MyPortalPrinter $name)) { Write-Error "The printer '$name' is not installed on this device."; exit 3 }
    if ($newName -ne $name -and (Get-MyPortalPrinter $newName)) {
        Write-Error "A printer named '$newName' already exists on this device."; exit 4
    }
    if (Get-Command Rename-Printer -ErrorAction SilentlyContinue) {
        Rename-Printer -Name $name -NewName $newName
    } else {
        $printer = Get-MyPortalPrinter $name
        $outcome = Invoke-CimMethod -InputObject $printer -MethodName RenamePrinter -Arguments @{ NewPrinterName = $newName }
        if ($outcome.ReturnValue -ne 0) { Write-Error "Windows could not rename the printer (error $($outcome.ReturnValue))."; exit 5 }
    }
    Write-MyPortalResult 'renamed' "Renamed printer '$name' to '$newName'."
    exit 0
}

if ($action -eq 'remove') {
    $removed = New-Object System.Collections.Generic.List[string]
    $printer = Get-MyPortalPrinter $name
    if ($printer) {
        if (Get-Command Remove-Printer -ErrorAction SilentlyContinue) {
            Remove-Printer -Name $name
        } else {
            Remove-CimInstance -InputObject $printer
        }
        $removed.Add('the device')
    }
    # \\server\printer connections: per-machine and each signed-in user's own.
    if ($name.StartsWith('\\')) {
        $parts = $name.Substring(2) -split '\\', 2
        if ($parts.Count -eq 2) {
            $key = ',,' + $parts[0] + ',' + $parts[1]
            $machine = "Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Print\Connections\$key"
            if (Test-Path -LiteralPath $machine) {
                Remove-Item -LiteralPath $machine -Recurse -Force
                $removed.Add('all users')
            }
            Get-ChildItem 'Registry::HKEY_USERS' -ErrorAction SilentlyContinue |
                Where-Object { $_.PSChildName -match '^S-1-5-21-[\d-]+$' } | ForEach-Object {
                    $path = "Registry::HKEY_USERS\$($_.PSChildName)\Printers\Connections\$key"
                    if (Test-Path -LiteralPath $path) {
                        Remove-Item -LiteralPath $path -Recurse -Force
                        try {
                            $user = (New-Object Security.Principal.SecurityIdentifier($_.PSChildName)).Translate([Security.Principal.NTAccount]).Value
                        } catch { $user = $_.PSChildName }
                        $removed.Add($user)
                    }
                }
        }
    }
    if ($removed.Count) {
        Write-MyPortalResult 'removed' "Removed printer '$name' from $($removed -join ', ')."
    } else {
        Write-MyPortalResult 'removed' "The printer '$name' was already gone."
    }
    exit 0
}

Write-Error "Unknown printer action '$action'."
exit 2
