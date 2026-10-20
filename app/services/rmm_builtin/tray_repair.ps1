# Repairs the MyPortal Tray Agent so it enrols again from scratch. It stops the tray
# service, deletes the tray state and cached tray config from ProgramData, removes the
# enrolled device ID from HKLM\SOFTWARE\MyPortal\Tray and starts the service again.
# The service then re-enrols with the install token the installer saved, which is left
# alone, as are the portal address and the rest of the tray's settings.
$serviceName = 'MyPortalTrayService'
$programData = if ($env:ProgramData) { $env:ProgramData } else { 'C:\ProgramData' }
$trayDir = Join-Path $programData 'MyPortal\tray'
$failed = $false

$service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if (-not $service) {
    Write-Error "The $serviceName service is not installed. Reinstall the MyPortal Tray Agent."
    exit 1
}

Write-Output "Stopping $serviceName..."
try {
    if ($service.Status -ne 'Stopped') {
        Stop-Service -Name $serviceName -Force -ErrorAction Stop
        $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    }
} catch {
    # A hung service would keep its files open; end its process instead.
    Write-Output "The service did not stop cleanly ($($_.Exception.Message)); ending its process."
    $process = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessId) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

foreach ($name in @('tray-state.json', 'tray-config.json')) {
    $path = Join-Path $trayDir $name
    if (Test-Path -LiteralPath $path) {
        try {
            Remove-Item -LiteralPath $path -Force -ErrorAction Stop
            Write-Output "Deleted $path"
        } catch {
            Write-Output "Could not delete ${path}: $($_.Exception.Message)"
            $failed = $true
        }
    } else {
        Write-Output "$path was not present"
    }
}

# Open the 64-bit registry view so a 32-bit PowerShell still edits the tray's key.
try {
    $hklm = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine', 'Registry64')
    $key = $hklm.OpenSubKey('SOFTWARE\MyPortal\Tray', $true)
    if ($key -and ($key.GetValueNames() -contains 'DeviceUID')) {
        $key.DeleteValue('DeviceUID')
        Write-Output 'Removed DeviceUID from HKLM\SOFTWARE\MyPortal\Tray'
    } else {
        Write-Output 'HKLM\SOFTWARE\MyPortal\Tray had no DeviceUID'
    }
    if ($key) { $key.Close() }
    $hklm.Close()
} catch {
    Write-Output "Could not remove DeviceUID: $($_.Exception.Message)"
    $failed = $true
}

Write-Output "Starting $serviceName..."
try {
    Start-Service -Name $serviceName -ErrorAction Stop
    (Get-Service -Name $serviceName -ErrorAction Stop).WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    Write-Output "$serviceName is running. The tray agent enrols again in the next few seconds."
} catch {
    Write-Error "Could not start ${serviceName}: $($_.Exception.Message)"
    exit 1
}

if ($failed) {
    Write-Error 'The tray service restarted, but part of the repair failed (see above).'
    exit 1
}
exit 0
